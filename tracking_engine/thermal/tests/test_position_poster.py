"""Tests for thermal position POST payloads."""

from __future__ import annotations

from tracking_engine.thermal.config import ThermalConfig, RoomThermalSpec
from tracking_engine.thermal.position_poster import ThermalPositionPoster


def _minimal_cfg(*, dry_run: bool = True) -> ThermalConfig:
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
        poster_dry_run=dry_run,
        verify_tls=True,
        event_throttle_sec=30.0,
        body_temp_min_c=28.0,
        body_temp_max_c=38.5,
        motion_threshold_normalized=0.02,
        background_alpha=0.02,
        positions_enabled=True,
        position_post_hz=2.0,
        position_ema_alpha=0.25,
        position_hold_sec=2.0,
        min_blob_pixels=3,
        suppress_when_door_closed=False,
        maro_api_base="http://127.0.0.1:8420",
        maro_cache_dir="calibration/maro_cache",
        rooms=[],
    )


def test_build_payload_shape():
    poster = ThermalPositionPoster(_minimal_cfg(), plan_w_px=2700, plan_h_px=1324)
    payload = poster.build_payload("SZ", 1000.0, (500.0, 600.0))
    assert payload["cam_id"] == "thermal_sz"
    assert len(payload["persons"]) == 1
    p = payload["persons"][0]
    assert p["zone"] == "SZ"
    assert p["privacy"] is True
    assert p["position_source"] == "thermal"
    assert p["x"] == 500.0
    assert p["y"] == 600.0
    poster.close()


def test_post_dry_run_rescales_to_legacy_mm():
    cfg = _minimal_cfg(dry_run=True)
    poster = ThermalPositionPoster(cfg, plan_w_px=2700, plan_h_px=1324)
    ok, body = poster.post("BZ", 1.0, (270.0, 132.4))
    assert ok
    import json

    data = json.loads(body)
    person = data["persons"][0]
    assert person["x"] > 1000.0
    assert person["y"] > 500.0
    poster.close()
