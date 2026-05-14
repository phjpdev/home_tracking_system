"""Smoke tests for blob extraction + posture inference + floor projection."""

from __future__ import annotations

import math

import numpy as np

from tracking_engine.thermal.blob import analyse_thermal_frame, mount_to_floor_mm


FRAME_SHAPE = (24, 32)
MOUNT_XYZ = (12150, 6500, 3000)   # SZ ceiling mount
FOV_H = 55.0
FOV_V = 35.0


def _ambient_frame(temp_c: float = 22.0) -> np.ndarray:
    return np.full(FRAME_SHAPE, temp_c, dtype=np.float32)


def _stamp_blob(frame: np.ndarray, row0: int, col0: int, height: int, width: int, body_c: float = 33.0) -> np.ndarray:
    out = frame.copy()
    out[row0 : row0 + height, col0 : col0 + width] = body_c
    return out


def test_floor_projection_centre_is_mount():
    rows, cols = FRAME_SHAPE
    fx, fy = mount_to_floor_mm(
        (rows - 1) / 2.0,
        (cols - 1) / 2.0,
        frame_shape=FRAME_SHAPE,
        mount_xyz_mm=MOUNT_XYZ,
        fov_h_deg=FOV_H,
        fov_v_deg=FOV_V,
    )
    assert math.isclose(fx, MOUNT_XYZ[0], abs_tol=1e-3)
    assert math.isclose(fy, MOUNT_XYZ[1], abs_tol=1e-3)


def test_vertical_blob_classified_vertical():
    bg = _ambient_frame()
    frame = _stamp_blob(bg, row0=8, col0=15, height=10, width=2, body_c=34.0)
    blob, _ = analyse_thermal_frame(
        frame_c=frame,
        background_c=bg.copy(),
        background_alpha=0.0,
        body_temp_min_c=28.0,
        body_temp_max_c=39.0,
        frame_shape=FRAME_SHAPE,
        mount_xyz_mm=MOUNT_XYZ,
        fov_h_deg=FOV_H,
        fov_v_deg=FOV_V,
    )
    assert blob.posture == "vertical"
    assert blob.area_pixels >= 18


def test_horizontal_blob_classified_horizontal():
    bg = _ambient_frame()
    frame = _stamp_blob(bg, row0=12, col0=4, height=2, width=12, body_c=34.0)
    blob, _ = analyse_thermal_frame(
        frame_c=frame,
        background_c=bg.copy(),
        background_alpha=0.0,
        body_temp_min_c=28.0,
        body_temp_max_c=39.0,
        frame_shape=FRAME_SHAPE,
        mount_xyz_mm=MOUNT_XYZ,
        fov_h_deg=FOV_H,
        fov_v_deg=FOV_V,
    )
    assert blob.posture == "horizontal"
    assert blob.area_pixels >= 22


def test_empty_frame_yields_no_blob():
    bg = _ambient_frame()
    blob, _ = analyse_thermal_frame(
        frame_c=bg.copy(),
        background_c=bg.copy(),
        background_alpha=0.0,
        body_temp_min_c=28.0,
        body_temp_max_c=39.0,
        frame_shape=FRAME_SHAPE,
        mount_xyz_mm=MOUNT_XYZ,
        fov_h_deg=FOV_H,
        fov_v_deg=FOV_V,
    )
    assert blob.area_pixels == 0
    assert blob.posture == "unknown"


if __name__ == "__main__":
    test_floor_projection_centre_is_mount()
    test_vertical_blob_classified_vertical()
    test_horizontal_blob_classified_horizontal()
    test_empty_frame_yields_no_blob()
    print("[ok] all thermal blob tests passed")
