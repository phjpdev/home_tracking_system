"""Per-room runner: feeds thermal frames into PrivacyThermalFallDetector."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from camera_placement_plan.thermal_fall_detection import (
    FallAlarm,
    PrivacyThermalFallDetector,
    ThermalBlobFrame,
    ThermalFallDetectionConfig,
)

from .blob import ThermalBlob, analyse_thermal_frame
from .config import RoomThermalSpec, ThermalConfig


@dataclass
class _RoomState:
    background: Optional[np.ndarray] = None
    last_pixel: Optional[tuple[float, float]] = None
    last_event_ts: float = 0.0
    leak_active: bool = False


class RoomFallRunner:
    """One instance per privacy room. Stateful."""

    def __init__(
        self,
        *,
        room_spec: RoomThermalSpec,
        thermal_cfg: ThermalConfig,
        fall_cfg: ThermalFallDetectionConfig,
    ):
        self.room_spec = room_spec
        self.thermal_cfg = thermal_cfg
        self.fall_cfg = fall_cfg
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

    def feed_frame(self, frame_c_flat: list[float], frame_ts: float) -> list[FallAlarm]:
        rows, cols = self.room_spec.frame_shape
        if len(frame_c_flat) != rows * cols:
            return []
        arr = np.asarray(frame_c_flat, dtype=np.float32).reshape(rows, cols)
        blob, new_background = analyse_thermal_frame(
            frame_c=arr,
            background_c=self._state.background,
            background_alpha=self.thermal_cfg.background_alpha,
            body_temp_min_c=self.thermal_cfg.body_temp_min_c,
            body_temp_max_c=self.thermal_cfg.body_temp_max_c,
            frame_shape=self.room_spec.frame_shape,
            mount_xyz_mm=self.room_spec.mount_xyz_mm,
            fov_h_deg=self.room_spec.fov_h_deg,
            fov_v_deg=self.room_spec.fov_v_deg,
            prev_blob_pixel=self._state.last_pixel,
        )
        self._state.background = new_background
        if blob.area_pixels >= 3:
            self._state.last_pixel = (blob.centroid_row, blob.centroid_col)

        if blob.area_pixels < 3 or not _finite(blob.centroid_x_mm):
            return []

        bf = ThermalBlobFrame(
            t_sec=float(frame_ts),
            posture=blob.posture,
            centroid_x_mm=blob.centroid_x_mm,
            centroid_y_mm=blob.centroid_y_mm,
            motion_normalized=blob.motion_normalized,
            on_floor=blob.on_floor,
        )
        alarms = self.detector.update(bf, water_leak_active=self._state.leak_active)
        if not alarms:
            return []

        out: list[FallAlarm] = []
        for a in alarms:
            if frame_ts - self._state.last_event_ts < self.thermal_cfg.event_throttle_sec:
                continue
            self._state.last_event_ts = float(frame_ts)
            out.append(a)
        return out

    def snapshot_blob(self) -> Optional[ThermalBlob]:
        # Diagnostic helper for unit tests / Prometheus exposition.
        return None


def _finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))


def load_fall_config(cameras_config_path: Path) -> ThermalFallDetectionConfig:
    return ThermalFallDetectionConfig.from_json_path(cameras_config_path)
