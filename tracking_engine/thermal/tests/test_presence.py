"""Tests for thermal presence smoothing and geometry."""

from __future__ import annotations

import math

from camera_placement_plan.thermal_fall_detection import ThermalFallDetectionConfig

from tracking_engine.pipeline.thermal_gate import apply_thermal_gate
from tracking_engine.thermal.config import RoomThermalSpec, ThermalConfig
from tracking_engine.thermal.detector_runner import RoomFallRunner
from tracking_engine.thermal.geometry import clip_point_to_polygon, point_in_polygon


FRAME_SHAPE = (24, 32)
MOUNT = (12150, 6500, 3000)


def _cfg() -> ThermalConfig:
    return ThermalConfig(
        enabled=True,
        mqtt_host="127.0.0.1",
        mqtt_port=1883,
        mqtt_user=None,
        mqtt_password=None,
        topic_prefix="home",
        mqtt_publish_presence=False,
        events_url="http://127.0.0.1:8420/tracking/events",
        positions_url="http://127.0.0.1:8420/tracking/positions",
        poster_timeout_sec=3.0,
        poster_dry_run=True,
        verify_tls=True,
        event_throttle_sec=30.0,
        body_temp_min_c=28.0,
        body_temp_max_c=38.5,
        motion_threshold_normalized=0.02,
        background_alpha=0.02,
        positions_enabled=True,
        position_post_hz=10.0,
        position_ema_alpha=0.5,
        position_hold_sec=1.0,
        min_blob_pixels=3,
        suppress_when_door_closed=False,
        maro_api_base="http://127.0.0.1:8420",
        maro_cache_dir="calibration/maro_cache",
        rooms=[],
    )


def _runner(poly_px: list[tuple[float, float]]) -> RoomFallRunner:
    spec = RoomThermalSpec(
        room="SZ",
        mount_xyz_mm=MOUNT,
        mount_offset_mm=(0.0, 0.0),
        fov_h_deg=55.0,
        fov_v_deg=35.0,
        frame_shape=FRAME_SHAPE,
        polygon_mm=[(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        has_leak_probe=False,
    )
    fall_cfg = ThermalFallDetectionConfig(
        rapid_transition_max_s=1.0,
        slow_transition_min_s=3.0,
        sz_still_confirmation_s=30.0,
        bz_still_horizontal_s=20.0,
        motion_threshold_normalized=0.02,
        sz_suppress_if_slow_to_rest_zone=True,
        privacy_thermal_zones_mm={
            "SZ": [(0, 0), (10000, 0), (10000, 10000), (0, 10000)],
        },
        sz_rest_zone_polygon_mm=None,
    )

    def mm_to_plan(x_mm: float, y_mm: float) -> tuple[float, float]:
        return x_mm / 10.0, y_mm / 10.0

    return RoomFallRunner(
        room_spec=spec,
        thermal_cfg=_cfg(),
        fall_cfg=fall_cfg,
        mm_to_plan_px=mm_to_plan,
        polygon_plan_px=poly_px,
    )


def _warm_frame() -> list[float]:
    temps = [22.0] * (24 * 32)
    for r in range(10, 14):
        for c in range(14, 18):
            temps[r * 32 + c] = 34.0
    return temps


def test_point_in_polygon_square():
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert point_in_polygon(5.0, 5.0, poly)
    assert clip_point_to_polygon(5.0, 5.0, poly) == (5.0, 5.0)
    assert clip_point_to_polygon(50.0, 5.0, poly) is None


def test_feed_frame_presence_and_rate_limit():
    poly = [(0.0, 0.0), (2000.0, 0.0), (2000.0, 2000.0), (0.0, 2000.0)]
    runner = _runner(poly)
    cold = [22.0] * (24 * 32)
    runner.feed_frame(cold, 0.0)
    warm = _warm_frame()
    r1 = runner.feed_frame(warm, 0.1)
    assert r1.in_room
    assert r1.plan_px is not None
    assert r1.should_post_position
    r2 = runner.feed_frame(warm, 0.15)
    assert not r2.should_post_position


def test_hold_after_blob_lost():
    poly = [(0.0, 0.0), (2000.0, 0.0), (2000.0, 2000.0), (0.0, 2000.0)]
    runner = _runner(poly)
    cold = [22.0] * (24 * 32)
    runner.feed_frame(cold, 0.0)
    warm = _warm_frame()
    runner.feed_frame(warm, 0.1)
    r = runner.feed_frame(cold, 0.5)
    assert r.in_room


def test_thermal_gate_drops_optical_inside_sz():
    poly = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    persons = [{"id": "t1", "x": 50.0, "y": 50.0, "zone": "SZ"}]
    out = apply_thermal_gate(
        persons,
        presence={"SZ": True, "BZ": False},
        privacy_zones_plan_px={"SZ": poly},
        buffer_px=10.0,
    )
    assert out == []
    out2 = apply_thermal_gate(
        persons,
        presence={"SZ": False, "BZ": False},
        privacy_zones_plan_px={"SZ": poly},
        buffer_px=10.0,
    )
    assert len(out2) == 1
