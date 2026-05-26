"""Per-camera homography fit on a shared set of world positions.

The frontend collects positions ``P_k`` (in floor-plan millimetres) and,
for each position, the foot-pixel click in every camera that saw the
operator. We then fit one ``H_c`` per camera against the subset of
positions that camera saw — same call as
:func:`tools.calibrate_homography._compute_homography` so the JSON it
produces is interchangeable with the legacy single-camera tool.

The novelty (vs. ``tools/calibrate_homography.py``) is that the world
coordinates are *shared* across cameras: at position ``P_k`` every
camera that saw the operator is pinned to the same ``(x_mm, y_mm)``.
This is what gives cross-camera consistency in overlap zones; the
:func:`fit_all` return also includes a per-position
``cross_camera_disagreement_mm`` metric that the UI shows live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class Position:
    """One operator standing position visible from one or more cameras."""

    id: str
    world_xy_mm: tuple[float, float]
    clicks: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class CameraFit:
    """Result of :func:`cv2.findHomography` for one camera."""

    cam_id: str
    num_points: int
    image_points: list[tuple[float, float]]
    world_points: list[tuple[float, float]]
    used_position_ids: list[str]
    H: Optional[np.ndarray] = None
    residual_mm_mean: float = 0.0
    residual_mm_max: float = 0.0
    error: Optional[str] = None


@dataclass
class PositionStat:
    """Live cross-camera consistency for one shared position."""

    position_id: str
    contributing_cams: list[str]
    disagreement_mm: Optional[float] = None
    per_cam_projection_mm: dict[str, tuple[float, float]] = field(default_factory=dict)


def _fit_one(cam_id: str, src: np.ndarray, dst: np.ndarray, ids: list[str]) -> CameraFit:
    n = src.shape[0]
    image_points = [(float(u), float(v)) for u, v in src]
    world_points = [(float(x), float(y)) for x, y in dst]
    fit = CameraFit(
        cam_id=cam_id,
        num_points=n,
        image_points=image_points,
        world_points=world_points,
        used_position_ids=list(ids),
    )
    if n < 4:
        fit.error = f"need >=4 clicked positions, got {n}"
        return fit
    H, _inliers = cv2.findHomography(
        src.astype(np.float64),
        dst.astype(np.float64),
        method=cv2.RANSAC,
        ransacReprojThreshold=8.0,
    )
    if H is None:
        fit.error = "findHomography returned None — points may be colinear"
        return fit
    src_h = np.hstack([src.astype(np.float64), np.ones((n, 1), dtype=np.float64)])
    proj = (H @ src_h.T).T
    w = proj[:, 2:3]
    w = np.where(np.abs(w) < 1e-9, 1e-9, w)
    proj_xy = proj[:, :2] / w
    residuals = np.linalg.norm(proj_xy - dst.astype(np.float64), axis=1)
    fit.H = H
    fit.residual_mm_mean = float(np.mean(residuals)) if n else 0.0
    fit.residual_mm_max = float(np.max(residuals)) if n else 0.0
    return fit


def fit_all(
    positions: Iterable[Position],
) -> tuple[dict[str, CameraFit], dict[str, PositionStat]]:
    """Fit one homography per camera and compute per-position consistency.

    Parameters
    ----------
    positions
        Iterable of :class:`Position`. Order is preserved in the
        per-camera ``used_position_ids``.

    Returns
    -------
    cam_fits
        ``{cam_id: CameraFit}`` for every camera that has >=1 click. A
        camera with too few points returns a :class:`CameraFit` whose
        ``H is None`` and ``error`` is set.
    position_stats
        ``{position_id: PositionStat}`` for every position, including
        positions seen by only one camera (their
        ``disagreement_mm`` is ``None``).
    """

    positions_list = list(positions)

    by_cam: dict[str, dict[str, list]] = {}
    for pos in positions_list:
        for cam_id, uv in pos.clicks.items():
            d = by_cam.setdefault(
                cam_id, {"src": [], "dst": [], "ids": []}
            )
            d["src"].append([float(uv[0]), float(uv[1])])
            d["dst"].append([float(pos.world_xy_mm[0]), float(pos.world_xy_mm[1])])
            d["ids"].append(pos.id)

    cam_fits: dict[str, CameraFit] = {}
    for cam_id, d in by_cam.items():
        src = np.asarray(d["src"], dtype=np.float64)
        dst = np.asarray(d["dst"], dtype=np.float64)
        cam_fits[cam_id] = _fit_one(cam_id, src, dst, d["ids"])

    position_stats: dict[str, PositionStat] = {}
    for pos in positions_list:
        cams = [c for c in pos.clicks if cam_fits.get(c) and cam_fits[c].H is not None]
        stat = PositionStat(position_id=pos.id, contributing_cams=list(cams))
        projections: dict[str, tuple[float, float]] = {}
        for cam_id in cams:
            uv = pos.clicks[cam_id]
            H = cam_fits[cam_id].H
            assert H is not None
            vec = np.array([uv[0], uv[1], 1.0], dtype=np.float64)
            xyw = H @ vec
            if abs(xyw[2]) < 1e-9:
                continue
            projections[cam_id] = (float(xyw[0] / xyw[2]), float(xyw[1] / xyw[2]))
        stat.per_cam_projection_mm = projections
        if len(projections) >= 2:
            pts = list(projections.values())
            max_d = 0.0
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    dx = pts[i][0] - pts[j][0]
                    dy = pts[i][1] - pts[j][1]
                    d_mm = float(np.hypot(dx, dy))
                    if d_mm > max_d:
                        max_d = d_mm
            stat.disagreement_mm = max_d
        position_stats[pos.id] = stat

    return cam_fits, position_stats
