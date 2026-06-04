"""Tests for stereo foot fusion."""

from __future__ import annotations

from tracking_engine.pipeline.stereo_foot import (
    StereoFootConfig,
    StereoFootCoordinator,
    fuse_stereo_plan_points,
)


def test_fuse_stereo_plan_points_agree() -> None:
    out = fuse_stereo_plan_points((100.0, 200.0), (110.0, 205.0), max_plan_distance_px=150.0)
    assert out is not None
    x, y, reproj = out
    assert abs(x - 105.0) < 0.01
    assert abs(y - 202.5) < 0.01
    assert reproj < 10.0


def test_fuse_stereo_plan_points_reject() -> None:
    assert fuse_stereo_plan_points((0.0, 0.0), (500.0, 500.0), max_plan_distance_px=100.0) is None


def test_merge_fusion_rows() -> None:
    coord = StereoFootCoordinator(
        StereoFootConfig(
            pairs=[("cam_a", "cam_b")],
            max_match_plan_px=150.0,
            max_reproj_error_px=12.0,
        )
    )
    rows = [
        {"cam_id": "cam_a", "id": "t1", "x": 100, "y": 200, "privacy": False, "zone": "Yoga"},
        {"cam_id": "cam_b", "id": "t2", "x": 105, "y": 202, "privacy": False, "zone": "Yoga"},
        {"cam_id": "cam_c", "id": "t3", "x": 50, "y": 50, "privacy": False, "zone": "Hallway"},
    ]
    merged = coord.merge_fusion_rows(rows)
    stereo = [r for r in merged if r.get("position_source") == "stereo"]
    assert len(stereo) == 1
    assert stereo[0]["x"] == 102 or stereo[0]["x"] == 103
    assert any(r["cam_id"] == "cam_c" for r in merged)
