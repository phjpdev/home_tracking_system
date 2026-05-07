"""
Thermal IR privacy-zone fall detection (SZ + BZ)
==================================================

Implements deployment logic for Panasonic AMG8833 (8x8) or MLX90640-class
low-resolution thermal blobs: posture trajectory, stillness confirmation,
and optional water-leak boost in BZ.

Coordinates use the same floor-plan envelope frame as ``generate_camera_plan.py``
(+x east, +y south, mm). Upstream preprocessing (rolling background subtraction,
ellipse / PCA on the heat blob) should produce posture and motion before calling
:class:`PrivacyThermalFallDetector`.

See ``output/cameras_config.json`` keys ``privacy_thermal_zones_mm`` and
``thermal_fall_detection`` for tunable parameters exported from the plan generator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from matplotlib.path import Path as MplPath

Posture = Literal["vertical", "horizontal", "unknown"]
RoomPZ = Literal["SZ", "BZ"]


def polygon_path_mm(polygon_mm: list[tuple[int, int]]) -> MplPath:
    verts = [(float(x), float(y)) for x, y in polygon_mm]
    verts.append(verts[0])
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(verts) - 2) + [MplPath.CLOSEPOLY]
    return MplPath(verts, codes)


def point_in_polygon_mm(x_mm: float, y_mm: float, polygon_mm: list[tuple[int, int]]) -> bool:
    return polygon_path_mm(polygon_mm).contains_point((x_mm, y_mm))


@dataclass
class ThermalFallDetectionConfig:
    """Tunable parameters; mirrors ``thermal_fall_detection`` in cameras_config.json."""

    rapid_transition_max_s: float = 1.0
    slow_transition_min_s: float = 3.0
    sz_still_confirmation_s: float = 30.0
    bz_still_horizontal_s: float = 20.0
    motion_threshold_normalized: float = 0.02
    sz_suppress_if_slow_to_rest_zone: bool = True

    privacy_thermal_zones_mm: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    sz_rest_zone_polygon_mm: list[tuple[int, int]] | None = None

    @classmethod
    def from_cameras_config_dict(cls, d: dict[str, Any]) -> ThermalFallDetectionConfig:
        z = d.get("privacy_thermal_zones_mm") or {}
        zones = {
            k: [(int(p[0]), int(p[1])) for p in v]
            for k, v in z.items()
        }
        fd = d.get("thermal_fall_detection") or {}
        rest = fd.get("sz_rest_zone_polygon_mm")
        rest_poly = (
            [(int(p[0]), int(p[1])) for p in rest] if rest else None
        )
        return cls(
            rapid_transition_max_s=float(fd.get("rapid_transition_max_s", 1.0)),
            slow_transition_min_s=float(fd.get("slow_transition_min_s", 3.0)),
            sz_still_confirmation_s=float(fd.get("sz_still_confirmation_s", 30.0)),
            bz_still_horizontal_s=float(fd.get("bz_still_horizontal_s", 20.0)),
            motion_threshold_normalized=float(fd.get("motion_threshold_normalized", 0.02)),
            sz_suppress_if_slow_to_rest_zone=bool(
                fd.get("sz_suppress_if_slow_to_rest_zone", True)
            ),
            privacy_thermal_zones_mm=zones,
            sz_rest_zone_polygon_mm=rest_poly,
        )

    @classmethod
    def from_json_path(cls, path: str | Path) -> ThermalFallDetectionConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_cameras_config_dict(data)


@dataclass
class ThermalBlobFrame:
    """One time step of thermal-derived state for a single room."""

    t_sec: float
    posture: Posture
    centroid_x_mm: float
    centroid_y_mm: float
    motion_normalized: float
    on_floor: bool = True


@dataclass
class FallAlarm:
    room: RoomPZ
    t_sec: float
    confidence: Literal["high", "medium", "low"]
    reason: str


class PrivacyThermalFallDetector:
    """
    Per-room stateful detector. Instantiate one per privacy room (SZ, BZ).

    **SZ:** rapid vertical→horizontal (<= ``rapid_transition_max_s``) plus
    ``sz_still_confirmation_s`` of stillness → **high** confidence fall.
    Slow transition (>= ``slow_transition_min_s``) or ending inside optional
    ``sz_rest_zone_polygon_mm`` suppresses alarm (going to bed / rest).

    **BZ:** horizontal, on-floor, still for ``bz_still_horizontal_s`` → **medium**
    fall; water leak concurrent → **high**.
    """

    def __init__(self, room: RoomPZ, config: ThermalFallDetectionConfig):
        self.room = room
        self.cfg = config
        polygon = config.privacy_thermal_zones_mm.get(room)
        if not polygon:
            raise ValueError(f"privacy_thermal_zones_mm missing polygon for {room}")
        self._poly = polygon

        self._prev_posture: Posture = "unknown"
        self._vertical_entry_t: float | None = None
        self._last_transition_dt: float | None = None
        self._still_streak_start_t: float | None = None

    def _in_zone(self, x_mm: float, y_mm: float) -> bool:
        return point_in_polygon_mm(x_mm, y_mm, self._poly)

    def _in_rest_zone(self, x_mm: float, y_mm: float) -> bool:
        rz = self.cfg.sz_rest_zone_polygon_mm
        if not rz or self.room != "SZ":
            return False
        return point_in_polygon_mm(x_mm, y_mm, rz)

    def update(
        self,
        frame: ThermalBlobFrame,
        water_leak_active: bool = False,
    ) -> list[FallAlarm]:
        """
        Advance state machine; return zero or one :class:`FallAlarm` events
        emitted on this frame.
        """
        alarms: list[FallAlarm] = []
        cfg = self.cfg
        motion_high = frame.motion_normalized > cfg.motion_threshold_normalized

        # --- Posture edge: vertical → horizontal (time since vertical pose began) ---
        if frame.posture == "vertical":
            if self._prev_posture != "vertical":
                self._vertical_entry_t = frame.t_sec
        elif frame.posture == "horizontal":
            if self._prev_posture == "vertical" and self._vertical_entry_t is not None:
                self._last_transition_dt = max(
                    0.0, frame.t_sec - self._vertical_entry_t
                )
        else:
            self._vertical_entry_t = None

        self._prev_posture = frame.posture

        poly_ok = self._in_zone(frame.centroid_x_mm, frame.centroid_y_mm)
        horiz_floor = (
            frame.posture == "horizontal"
            and frame.on_floor
            and poly_ok
        )

        if not horiz_floor:
            self._still_streak_start_t = None
            return alarms

        if motion_high:
            self._still_streak_start_t = None
            return alarms

        if self._still_streak_start_t is None:
            self._still_streak_start_t = frame.t_sec
        still_for = frame.t_sec - self._still_streak_start_t

        dt_tr = self._last_transition_dt
        rapid = dt_tr is not None and dt_tr <= cfg.rapid_transition_max_s
        slow = dt_tr is not None and dt_tr >= cfg.slow_transition_min_s

        if self.room == "BZ":
            if still_for >= cfg.bz_still_horizontal_s:
                if water_leak_active:
                    alarms.append(FallAlarm(
                        "BZ", frame.t_sec, "high",
                        "BZ horizontal still >= "
                        f"{cfg.bz_still_horizontal_s:.0f}s + water leak",
                    ))
                else:
                    alarms.append(FallAlarm(
                        "BZ", frame.t_sec, "medium",
                        "BZ horizontal still on floor >= "
                        f"{cfg.bz_still_horizontal_s:.0f}s (no furniture zone)",
                    ))
                self._reset_after_alarm()
            return alarms

        # --- SZ ---
        in_rest = self._in_rest_zone(frame.centroid_x_mm, frame.centroid_y_mm)

        def suppress_slow_bed() -> bool:
            """Going to bed: slow intentional lie-down, or stationary rest on bed/sofa."""
            if not cfg.sz_suppress_if_slow_to_rest_zone:
                return False
            if slow:
                return True
            if in_rest and not rapid:
                return True
            return False

        if still_for >= cfg.sz_still_confirmation_s:
            if suppress_slow_bed():
                self._still_streak_start_t = None
                return alarms
            conf: Literal["high", "medium", "low"] = (
                "high" if rapid else "medium")
            alarms.append(FallAlarm(
                "SZ", frame.t_sec, conf,
                "SZ horizontal still >= "
                f"{cfg.sz_still_confirmation_s:.0f}s; "
                f"transition_dt={dt_tr}s rapid={rapid}",
            ))
            self._reset_after_alarm()

        return alarms

    def _reset_after_alarm(self) -> None:
        self._still_streak_start_t = None
        self._last_transition_dt = None
        self._vertical_entry_t = None
        self._prev_posture = "unknown"


def default_config_near_script() -> ThermalFallDetectionConfig:
    """Load ``output/cameras_config.json`` next to this file if present."""
    here = Path(__file__).resolve().parent / "output" / "cameras_config.json"
    if here.exists():
        return ThermalFallDetectionConfig.from_json_path(here)
    return ThermalFallDetectionConfig()
