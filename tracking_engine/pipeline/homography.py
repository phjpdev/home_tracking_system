"""Dummy (linear) or matrix homography: foot pixel → floor plan mm."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np


@dataclass
class Calibration:
    cam_id: str
    mode: str
    floor_bounds_mm: dict[str, float]
    H: Optional[np.ndarray] = None
    calib_image_size: Optional[tuple[int, int]] = None


def load_calibration(path: Path, cam_id: str) -> Calibration:
    data = json.loads(path.read_text(encoding="utf-8"))
    if cam_id not in data:
        raise KeyError(f"camera {cam_id!r} missing in {path}")
    entry: dict[str, Any] = data[cam_id]
    mode = str(entry.get("mode", "dummy"))
    bounds = dict(entry.get("floor_bounds_mm", {}))
    H = None
    calib_size = None
    if mode == "homography":
        H = np.array(entry["H"], dtype=np.float64)
        if H.shape != (3, 3):
            raise ValueError("H must be 3×3 for homography mode")
        if "calib_image_width" in entry and "calib_image_height" in entry:
            calib_size = (int(entry["calib_image_width"]), int(entry["calib_image_height"]))
    return Calibration(
        cam_id=cam_id,
        mode=mode,
        floor_bounds_mm=bounds,
        H=H,
        calib_image_size=calib_size,
    )


def foot_point_to_mm(
    calib: Calibration,
    foot_u: float,
    foot_v: float,
    frame_w: int,
    frame_h: int,
) -> Tuple[float, float]:
    """Map foot-point (bottom-centre of bbox) to (x_mm, y_mm) on the floor plan."""

    if calib.mode == "dummy":
        x0 = float(calib.floor_bounds_mm["x_min"])
        x1 = float(calib.floor_bounds_mm["x_max"])
        y0 = float(calib.floor_bounds_mm["y_min"])
        y1 = float(calib.floor_bounds_mm["y_max"])
        nu = float(np.clip(foot_u / max(frame_w, 1), 0.0, 1.0))
        nv = float(np.clip(foot_v / max(frame_h, 1), 0.0, 1.0))
        x_mm = x0 + nu * (x1 - x0)
        y_mm = y0 + nv * (y1 - y0)
        return x_mm, y_mm

    if calib.mode == "homography" and calib.H is not None:
        u, v = foot_u, foot_v
        if calib.calib_image_size is not None:
            cw, ch = calib.calib_image_size
            if frame_w != cw or frame_h != ch:
                sx = cw / max(frame_w, 1)
                sy = ch / max(frame_h, 1)
                u, v = foot_u * sx, foot_v * sy
        vec = np.array([u, v, 1.0], dtype=np.float64)
        xyw = calib.H @ vec
        if abs(xyw[2]) < 1e-9:
            return 0.0, 0.0
        return float(xyw[0] / xyw[2]), float(xyw[1] / xyw[2])

    raise ValueError(f"unknown calibration mode {calib.mode!r}")
