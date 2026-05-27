"""Per-camera homography fit on shared floor-plan pixel landmarks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class Position:
    """One landmark on the Maro plan visible from one or more cameras."""

    id: str
    world_xy_plan_px: tuple[float, float]
    clicks: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def world_xy_mm(self) -> tuple[float, float]:
        """Legacy alias — same tuple, may be plan px not mm."""
        return self.world_xy_plan_px


@dataclass
class CameraFit:
    cam_id: str
    num_points: int
    image_points: list[tuple[float, float]]
    world_points: list[tuple[float, float]]
    used_position_ids: list[str]
    H: Optional[np.ndarray] = None
    residual_px_mean: float = 0.0
    residual_px_max: float = 0.0
    per_position_residual_px: dict[str, float] = field(default_factory=dict)
    worst_position_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def residual_mm_mean(self) -> float:
        return self.residual_px_mean

    @property
    def residual_mm_max(self) -> float:
        return self.residual_px_max


@dataclass
class PositionStat:
    position_id: str
    contributing_cams: list[str]
    disagreement_px: Optional[float] = None
    per_cam_projection_px: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def disagreement_mm(self) -> Optional[float]:
        return self.disagreement_px


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
        fit.error = f"need >=4 clicked landmarks, got {n}"
        return fit
    H, _inliers = cv2.findHomography(
        src.astype(np.float64),
        dst.astype(np.float64),
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
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
    fit.residual_px_mean = float(np.mean(residuals)) if n else 0.0
    fit.residual_px_max = float(np.max(residuals)) if n else 0.0
    for i, pid in enumerate(ids):
        fit.per_position_residual_px[pid] = float(residuals[i])
    if ids:
        worst_i = int(np.argmax(residuals))
        fit.worst_position_id = ids[worst_i]
    return fit


def fit_all(
    positions: Iterable[Position],
) -> tuple[dict[str, CameraFit], dict[str, PositionStat]]:
    positions_list = list(positions)

    by_cam: dict[str, dict[str, list]] = {}
    for pos in positions_list:
        for cam_id, uv in pos.clicks.items():
            d = by_cam.setdefault(cam_id, {"src": [], "dst": [], "ids": []})
            d["src"].append([float(uv[0]), float(uv[1])])
            d["dst"].append([float(pos.world_xy_plan_px[0]), float(pos.world_xy_plan_px[1])])
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
        stat.per_cam_projection_px = projections
        if len(projections) >= 2:
            pts = list(projections.values())
            max_d = 0.0
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    d_px = float(np.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
                    if d_px > max_d:
                        max_d = d_px
            stat.disagreement_px = max_d
        position_stats[pos.id] = stat

    return cam_fits, position_stats
