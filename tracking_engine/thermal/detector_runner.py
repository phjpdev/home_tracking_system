"""Per-room runner: feeds thermal frames into fall detection + position tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from camera_placement_plan.thermal_fall_detection import (
    FallAlarm,
    PrivacyThermalFallDetector,
    ThermalBlobFrame,
    ThermalFallDetectionConfig,
)

from .blob import ThermalBlob, analyse_thermal_frame
from .config import RoomThermalSpec, ThermalConfig
from .geometry import clip_point_to_polygon


@dataclass
class FeedFrameResult:
    alarms: list[FallAlarm]
    in_room: bool = False
    plan_px: Optional[tuple[float, float]] = None
    should_post_position: bool = False
    blob_area: int = 0
    leak_active: bool = False


@dataclass
class _RoomState:
    background: Optional[np.ndarray] = None
    last_pixel: Optional[tuple[float, float]] = None
    last_event_ts: float = 0.0
    leak_active: bool = False
    door_open: bool = True
    ema_plan_px: Optional[tuple[float, float]] = None
    last_blob_ts: float = 0.0
    last_post_ts: float = 0.0
    last_blob: Optional[ThermalBlob] = None
    in_room: bool = False


class RoomFallRunner:
    """One instance per privacy room. Stateful."""

    def __init__(
        self,
        *,
        room_spec: RoomThermalSpec,
        thermal_cfg: ThermalConfig,
        fall_cfg: ThermalFallDetectionConfig,
        mm_to_plan_px: Callable[[float, float], tuple[float, float]],
        polygon_plan_px: list[tuple[float, float]],
    ):
        self.room_spec = room_spec
        self.thermal_cfg = thermal_cfg
        self.fall_cfg = fall_cfg
        self._mm_to_plan_px = mm_to_plan_px
        self._polygon_plan_px = polygon_plan_px
        self.detector = PrivacyThermalFallDetector(
            room=room_spec.room,
            config=fall_cfg,
        )
        self._state = _RoomState()

    @property
    def room(self) -> str:
        return self.room_spec.room

    def set_leak(self, wet: bool) -> None:
        self._state.leak_active = bool(wet)

    def set_door(self, open_: bool) -> None:
        self._state.door_open = bool(open_)

    def feed_frame(self, frame_c_flat: list[float], frame_ts: float) -> FeedFrameResult:
        rows, cols = self.room_spec.frame_shape
        if len(frame_c_flat) != rows * cols:
            return FeedFrameResult(alarms=[])

        mount = (
            self.room_spec.mount_xyz_mm[0] + self.room_spec.mount_offset_mm[0],
            self.room_spec.mount_xyz_mm[1] + self.room_spec.mount_offset_mm[1],
            self.room_spec.mount_xyz_mm[2],
        )

        arr = np.asarray(frame_c_flat, dtype=np.float32).reshape(rows, cols)
        blob, new_background = analyse_thermal_frame(
            frame_c=arr,
            background_c=self._state.background,
            background_alpha=self.thermal_cfg.background_alpha,
            body_temp_min_c=self.thermal_cfg.body_temp_min_c,
            body_temp_max_c=self.thermal_cfg.body_temp_max_c,
            frame_shape=self.room_spec.frame_shape,
            mount_xyz_mm=mount,
            fov_h_deg=self.room_spec.fov_h_deg,
            fov_v_deg=self.room_spec.fov_v_deg,
            prev_blob_pixel=self._state.last_pixel,
        )
        self._state.background = new_background
        self._state.last_blob = blob

        alarms: list[FallAlarm] = []
        if blob.area_pixels >= self.thermal_cfg.min_blob_pixels:
            self._state.last_pixel = (blob.centroid_row, blob.centroid_col)
            self._state.last_blob_ts = float(frame_ts)

            if _finite(blob.centroid_x_mm):
                plan_raw = self._mm_to_plan_px(blob.centroid_x_mm, blob.centroid_y_mm)
                clipped = clip_point_to_polygon(plan_raw[0], plan_raw[1], self._polygon_plan_px)
                if clipped is not None:
                    self._update_ema(clipped, frame_ts)
                    self._state.in_room = True

            if _finite(blob.centroid_x_mm):
                bf = ThermalBlobFrame(
                    t_sec=float(frame_ts),
                    posture=blob.posture,
                    centroid_x_mm=blob.centroid_x_mm,
                    centroid_y_mm=blob.centroid_y_mm,
                    motion_normalized=blob.motion_normalized,
                    on_floor=blob.on_floor,
                )
                for a in self.detector.update(bf, water_leak_active=self._state.leak_active):
                    if frame_ts - self._state.last_event_ts < self.thermal_cfg.event_throttle_sec:
                        continue
                    self._state.last_event_ts = float(frame_ts)
                    alarms.append(a)

        in_room = self._presence_active(frame_ts)
        plan_px = self._state.ema_plan_px if in_room else None
        should_post = self._should_post_position(frame_ts, in_room)

        return FeedFrameResult(
            alarms=alarms,
            in_room=in_room,
            plan_px=plan_px,
            should_post_position=should_post,
            blob_area=blob.area_pixels,
            leak_active=self._state.leak_active,
        )

    def _update_ema(self, plan_px: tuple[float, float], _ts: float) -> None:
        a = self.thermal_cfg.position_ema_alpha
        if self._state.ema_plan_px is None:
            self._state.ema_plan_px = plan_px
            return
        prev = self._state.ema_plan_px
        self._state.ema_plan_px = (
            a * plan_px[0] + (1.0 - a) * prev[0],
            a * plan_px[1] + (1.0 - a) * prev[1],
        )

    def _presence_active(self, ts: float) -> bool:
        if self.thermal_cfg.suppress_when_door_closed and not self._state.door_open:
            return False
        if self._state.ema_plan_px is None:
            return False
        if ts - self._state.last_blob_ts <= self.thermal_cfg.position_hold_sec:
            return True
        return False

    def _should_post_position(self, ts: float, in_room: bool) -> bool:
        if not self.thermal_cfg.positions_enabled or not in_room:
            return False
        if self._state.ema_plan_px is None:
            return False
        hz = max(self.thermal_cfg.position_post_hz, 0.1)
        min_interval = 1.0 / hz
        if ts - self._state.last_post_ts < min_interval:
            return False
        self._state.last_post_ts = float(ts)
        return True

    def snapshot_blob(self) -> Optional[ThermalBlob]:
        return self._state.last_blob


def _finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))


def load_fall_config(cameras_config_path: Path) -> ThermalFallDetectionConfig:
    return ThermalFallDetectionConfig.from_json_path(cameras_config_path)
