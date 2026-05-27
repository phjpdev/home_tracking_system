"""Dummy (linear) or matrix homography: foot pixel → floor plan mm or Maro plan px."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from .maro_floorplan import COORD_MARO_PLAN_PX  # noqa: F401 — re-export


@dataclass
class Calibration:
    cam_id: str
    mode: str
    coordinate_space: str
    floor_bounds_mm: dict[str, float]
    plan_bounds_px: Optional[dict[str, float]] = None
    plan_size_px: Optional[tuple[int, int]] = None
    H: Optional[np.ndarray] = None
    calib_image_size: Optional[tuple[int, int]] = None


def load_calibration(path: Path, cam_id: str) -> Calibration:
    data = json.loads(path.read_text(encoding="utf-8"))
    if cam_id not in data:
        raise KeyError(f"camera {cam_id!r} missing in {path}")
    entry: dict[str, Any] = data[cam_id]
    mode = str(entry.get("mode", "dummy"))
    doc_space = str(data.get("_coordinate_space") or entry.get("coordinate_space") or "")
    bounds = dict(entry.get("floor_bounds_mm", {}))
    plan_bounds = entry.get("plan_bounds_px")
    plan_bounds_px = dict(plan_bounds) if isinstance(plan_bounds, dict) else None
    plan_size = None
    if "_plan_size_px" in data and isinstance(data["_plan_size_px"], (list, tuple)):
        plan_size = (int(data["_plan_size_px"][0]), int(data["_plan_size_px"][1]))
    H = None
    calib_size = None
    if mode == "homography":
        H = np.array(entry["H"], dtype=np.float64)
        if H.shape != (3, 3):
            raise ValueError("H must be 3×3 for homography mode")
        if "calib_image_width" in entry and "calib_image_height" in entry:
            calib_size = (int(entry["calib_image_width"]), int(entry["calib_image_height"]))
    coord = str(entry.get("coordinate_space") or doc_space or "legacy_mm")
    if entry.get("world_points_plan_px"):
        coord = COORD_MARO_PLAN_PX
    elif not doc_space and coord == "legacy_mm" and mode == "homography":
        logger.warning(
            "camera %s: legacy mm homography — recalibrate with calibrate_web for Maro plan px",
            cam_id,
        )
    return Calibration(
        cam_id=cam_id,
        mode=mode,
        coordinate_space=coord,
        floor_bounds_mm=bounds,
        plan_bounds_px=plan_bounds_px,
        plan_size_px=plan_size,
        H=H,
        calib_image_size=calib_size,
    )


def _scale_uv(
    foot_u: float, foot_v: float, frame_w: int, frame_h: int, calib: Calibration
) -> tuple[float, float]:
    u, v = foot_u, foot_v
    if calib.calib_image_size is not None:
        cw, ch = calib.calib_image_size
        if frame_w != cw or frame_h != ch:
            sx = cw / max(frame_w, 1)
            sy = ch / max(frame_h, 1)
            u, v = foot_u * sx, foot_v * sy
    return u, v


def _homography_project(calib: Calibration, foot_u: float, foot_v: float, frame_w: int, frame_h: int) -> Tuple[float, float]:
    if calib.H is None:
        raise ValueError("homography H missing")
    u, v = _scale_uv(foot_u, foot_v, frame_w, frame_h, calib)
    vec = np.array([u, v, 1.0], dtype=np.float64)
    xyw = calib.H @ vec
    if abs(xyw[2]) < 1e-9:
        return 0.0, 0.0
    return float(xyw[0] / xyw[2]), float(xyw[1] / xyw[2])


def foot_point_to_mm(
    calib: Calibration,
    foot_u: float,
    foot_v: float,
    frame_w: int,
    frame_h: int,
) -> Tuple[float, float]:
    """Map foot-point to (x, y). Legacy name — may return plan pixels if calibrated as such."""

    if calib.coordinate_space == COORD_MARO_PLAN_PX:
        return foot_point_to_plan_px(calib, foot_u, foot_v, frame_w, frame_h)

    if calib.mode == "dummy":
        x0 = float(calib.floor_bounds_mm.get("x_min", 0))
        x1 = float(calib.floor_bounds_mm.get("x_max", frame_w))
        y0 = float(calib.floor_bounds_mm.get("y_min", 0))
        y1 = float(calib.floor_bounds_mm.get("y_max", frame_h))
        nu = float(np.clip(foot_u / max(frame_w, 1), 0.0, 1.0))
        nv = float(np.clip(foot_v / max(frame_h, 1), 0.0, 1.0))
        return x0 + nu * (x1 - x0), y0 + nv * (y1 - y0)

    if calib.mode == "homography" and calib.H is not None:
        return _homography_project(calib, foot_u, foot_v, frame_w, frame_h)

    raise ValueError(f"unknown calibration mode {calib.mode!r}")


def foot_point_to_plan_px(
    calib: Calibration,
    foot_u: float,
    foot_v: float,
    frame_w: int,
    frame_h: int,
) -> Tuple[float, float]:
    """Map foot-point to (plan_x, plan_y) on the Maro floor-plan image."""

    if calib.mode == "dummy":
        pb = calib.plan_bounds_px or {}
        pw = (calib.plan_size_px or (2700, 1324))[0]
        ph = (calib.plan_size_px or (2700, 1324))[1]
        x0 = float(pb.get("x_min", 0))
        x1 = float(pb.get("x_max", pw))
        y0 = float(pb.get("y_min", 0))
        y1 = float(pb.get("y_max", ph))
        nu = float(np.clip(foot_u / max(frame_w, 1), 0.0, 1.0))
        nv = float(np.clip(foot_v / max(frame_h, 1), 0.0, 1.0))
        return x0 + nu * (x1 - x0), y0 + nv * (y1 - y0)

    if calib.mode == "homography" and calib.H is not None:
        return _homography_project(calib, foot_u, foot_v, frame_w, frame_h)

    raise ValueError(f"unknown calibration mode {calib.mode!r}")
