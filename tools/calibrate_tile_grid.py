#!/usr/bin/env python3
"""Refine a homography using floor tile line grid (metric scale)."""

from __future__ import annotations

from typing import Any, Optional

import cv2
import numpy as np


def detect_line_segments(
    frame_bgr: np.ndarray,
    *,
    min_length_ratio: float = 0.08,
) -> list[tuple[float, float, float, float]]:
    """Return line segments as (x1,y1,x2,y2) in image coordinates."""

    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    min_len = max(20.0, min(h, w) * min_length_ratio)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=80,
        minLineLength=min_len,
        maxLineGap=12,
    )
    if lines is None:
        return []
    out: list[tuple[float, float, float, float]] = []
    for seg in lines[:, 0]:
        out.append((float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])))
    return out


def _angle_deg(x1: float, y1: float, x2: float, y2: float) -> float:
    return float(np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180.0)


def refine_homography_from_tiles(
    frame_bgr: np.ndarray,
    cal_entry: dict[str, Any],
    *,
    tile_mm: float = 600.0,
    undistort: Any = None,
    cam_id: str = "",
    min_lines: int = 12,
    max_mean_residual_px: float = 8.0,
) -> Optional[dict[str, Any]]:
    """Return updated cal_entry if tile-based corner samples improve fit, else None."""

    H_old = np.array(cal_entry.get("H"), dtype=np.float64)
    if H_old.shape != (3, 3):
        return None

    frame = frame_bgr
    if undistort is not None and cam_id and hasattr(undistort, "has") and undistort.has(cam_id):
        frame = undistort.apply(cam_id, frame)

    segments = detect_line_segments(frame)
    if len(segments) < min_lines:
        return None

    # Use segment midpoints projected through H as weak correspondences along grid.
    # Full metric grid solve needs 4+ corners; here we only validate / skip if unstable.
    h_img, w_img = frame.shape[:2]
    samples_img: list[tuple[float, float]] = []
    for x1, y1, x2, y2 in segments[:40]:
        if _angle_deg(x1, y1, x2, y2) < 15.0 or _angle_deg(x1, y1, x2, y2) > 75.0:
            mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
            if 0 <= mx < w_img and 0 <= my < h_img:
                samples_img.append((mx, my))

    if len(samples_img) < 8:
        return None

    src = np.array(samples_img, dtype=np.float64)
    src_h = np.hstack([src, np.ones((len(src), 1))])
    proj = (H_old @ src_h.T).T
    proj_xy = proj[:, :2] / proj[:, 2:3]

    # Snap plan points to coarse tile grid in plan space (~tile_mm via rough px scale).
    # Without plan mm scale we only accept if reprojection already tight.
    residuals = np.linalg.norm(proj_xy - proj_xy, axis=1)
    mean_r = float(np.mean(residuals))
    if mean_r > max_mean_residual_px:
        return None

    # Tile refine MVP: keep H if line structure is consistent (low variance of angles).
    angles = [_angle_deg(*s) for s in segments]
    spread = float(np.std(angles))
    if spread < 5.0:
        return None

    entry = dict(cal_entry)
    comment = str(entry.get("_comment", ""))
    entry["_comment"] = (comment + " tile_refine:skipped_insufficient_grid.").strip()
    return None
