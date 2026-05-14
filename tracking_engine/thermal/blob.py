"""Heat-blob extraction + posture / centroid inference for one MLX90640 frame.

Inputs:  raw 24 x 32 temperature grid (Celsius), the current rolling
         background, and the room mount/FOV geometry.
Outputs: a :class:`ThermalBlob` carrying posture, normalised motion, and the
         floor-plan centroid in mm — exactly what
         :class:`tracking_engine.camera_placement_plan.thermal_fall_detection
         .PrivacyThermalFallDetector` consumes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np

Posture = Literal["vertical", "horizontal", "unknown"]


@dataclass(frozen=True)
class ThermalBlob:
    posture: Posture
    centroid_x_mm: float
    centroid_y_mm: float
    centroid_row: float       # raw sensor-pixel centroid (for debugging)
    centroid_col: float
    area_pixels: int
    motion_normalized: float
    on_floor: bool            # always True for MLX90640 ceiling view


def _largest_connected_component(mask: np.ndarray) -> np.ndarray:
    """Return a boolean mask of the largest 4-connected component, or empty."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    next_label = 0
    sizes: list[int] = [0]
    for r in range(h):
        for c in range(w):
            if not mask[r, c] or labels[r, c]:
                continue
            next_label += 1
            sizes.append(0)
            stack = [(r, c)]
            while stack:
                rr, cc = stack.pop()
                if rr < 0 or rr >= h or cc < 0 or cc >= w:
                    continue
                if not mask[rr, cc] or labels[rr, cc]:
                    continue
                labels[rr, cc] = next_label
                sizes[-1] += 1
                stack.extend(((rr + 1, cc), (rr - 1, cc), (rr, cc + 1), (rr, cc - 1)))
    if next_label == 0:
        return np.zeros_like(mask, dtype=bool)
    best = int(np.argmax(sizes[1:])) + 1
    return labels == best


def mount_to_floor_mm(
    sensor_row: float,
    sensor_col: float,
    *,
    frame_shape: tuple[int, int],
    mount_xyz_mm: tuple[int, int, int],
    fov_h_deg: float,
    fov_v_deg: float,
) -> tuple[float, float]:
    """Project a (row, col) inside the MLX90640 frame to floor (x_mm, y_mm).

    The sensor is ceiling-mounted, lens pointing straight down, with the
    image's wider 32-pixel axis aligned with +x_mm. Floor is at z = 0.

    For a pinhole at height ``z`` and FOV ``alpha`` along an axis, the
    physical extent on the floor is ``2 * z * tan(alpha / 2)``. Centre of
    the frame projects to directly below the mount.
    """
    rows, cols = frame_shape
    cx_mount, cy_mount, z = mount_xyz_mm
    half_w_mm = float(z) * math.tan(math.radians(fov_h_deg) / 2.0)
    half_h_mm = float(z) * math.tan(math.radians(fov_v_deg) / 2.0)
    # Normalised pixel coordinates centred on the optical axis.
    nx = (float(sensor_col) - (cols - 1) / 2.0) / ((cols - 1) / 2.0)
    ny = (float(sensor_row) - (rows - 1) / 2.0) / ((rows - 1) / 2.0)
    fx = cx_mount + nx * half_w_mm
    fy = cy_mount + ny * half_h_mm
    return fx, fy


def analyse_thermal_frame(
    *,
    frame_c: np.ndarray,
    background_c: Optional[np.ndarray],
    background_alpha: float,
    body_temp_min_c: float,
    body_temp_max_c: float,
    frame_shape: tuple[int, int],
    mount_xyz_mm: tuple[int, int, int],
    fov_h_deg: float,
    fov_v_deg: float,
    prev_blob_pixel: Optional[tuple[float, float]] = None,
) -> tuple[ThermalBlob, np.ndarray]:
    """Analyse one frame and return (blob, updated_background).

    ``frame_c`` is a (rows, cols) float array in degrees Celsius.
    ``background_c`` may be ``None`` on the first call; a fresh rolling
    background will be initialised.
    """
    arr = np.asarray(frame_c, dtype=np.float32).reshape(frame_shape)
    if background_c is None:
        background_c = arr.copy()
    else:
        background_c = (
            (1.0 - background_alpha) * background_c + background_alpha * arr
        ).astype(np.float32)

    delta = arr - background_c
    # Person mask: warmer than background AND in plausible body range.
    body_mask = (arr >= body_temp_min_c) & (arr <= body_temp_max_c) & (delta > 1.0)
    blob_mask = _largest_connected_component(body_mask)
    area = int(blob_mask.sum())
    if area < 3:
        # Nothing to track this tick.
        return (
            ThermalBlob(
                posture="unknown",
                centroid_x_mm=float("nan"),
                centroid_y_mm=float("nan"),
                centroid_row=float("nan"),
                centroid_col=float("nan"),
                area_pixels=0,
                motion_normalized=0.0,
                on_floor=True,
            ),
            background_c,
        )

    rs, cs = np.where(blob_mask)
    centroid_row = float(rs.mean())
    centroid_col = float(cs.mean())

    # PCA on blob pixels for posture.
    pts = np.stack([cs.astype(np.float64), rs.astype(np.float64)], axis=1)
    pts -= pts.mean(axis=0, keepdims=True)
    cov = np.cov(pts, rowvar=False)
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        eigvals = np.array([1.0, 1.0])
        eigvecs = np.eye(2)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    major_len = math.sqrt(max(float(eigvals[0]), 1e-9))
    minor_len = math.sqrt(max(float(eigvals[1]), 1e-9))
    aspect = major_len / max(minor_len, 1e-6)

    # Major-axis orientation angle, relative to the column (x) axis.
    major = eigvecs[:, 0]
    angle_deg = math.degrees(math.atan2(abs(major[1]), abs(major[0])))

    if aspect < 1.6:
        posture: Posture = "unknown"
    elif angle_deg >= 55.0:
        posture = "vertical"
    else:
        posture = "horizontal"

    floor_x, floor_y = mount_to_floor_mm(
        centroid_row,
        centroid_col,
        frame_shape=frame_shape,
        mount_xyz_mm=mount_xyz_mm,
        fov_h_deg=fov_h_deg,
        fov_v_deg=fov_v_deg,
    )

    motion_normalized = 0.0
    if prev_blob_pixel is not None and not (
        math.isnan(prev_blob_pixel[0]) or math.isnan(prev_blob_pixel[1])
    ):
        dr = centroid_row - prev_blob_pixel[0]
        dc = centroid_col - prev_blob_pixel[1]
        rows, cols = frame_shape
        denom = math.hypot(rows, cols)
        motion_normalized = float(math.hypot(dr, dc) / max(denom, 1.0))

    return (
        ThermalBlob(
            posture=posture,
            centroid_x_mm=float(floor_x),
            centroid_y_mm=float(floor_y),
            centroid_row=centroid_row,
            centroid_col=centroid_col,
            area_pixels=area,
            motion_normalized=motion_normalized,
            on_floor=True,
        ),
        background_c,
    )
