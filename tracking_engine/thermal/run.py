#!/usr/bin/env python3
"""Thermal pipeline entrypoint.

Subscribes to MQTT, runs per-room :class:`RoomFallRunner` instances, POSTs
fall events and live positions to Maro. Designed to run as its own systemd unit
alongside ``tracking-engine.service``.

Run:

    python -m tracking_engine.thermal.run --config tracking_engine/config.multi_camera.yaml
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from ..observability import configure_logging, metrics, start_metrics_server
from ..pipeline.maro_floorplan import load_maro_floorplan
from .config import ThermalConfig
from .detector_runner import RoomFallRunner, load_fall_config
from .mqtt_subscriber import (
    DoorStateMessage,
    HeartbeatMessage,
    LeakStateMessage,
    MqttSubscriber,
    ThermalFrameMessage,
)
from .position_poster import ThermalPositionPoster, make_mm_to_plan_px
from .poster_bridge import FallEventPoster
from .presence_publisher import PresenceMqttPublisher


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _polygon_plan_px(
    polygon_mm: list[tuple[int, int]],
    mm_to_plan: Any,
) -> list[tuple[float, float]]:
    return [mm_to_plan(float(x), float(y)) for x, y in polygon_mm]


def run(cfg_path: Path) -> int:
    cfg = _load_yaml(cfg_path)
    cfg_dir = cfg_path.parent

    obs_cfg = cfg.get("observability", {}) or {}
    configure_logging(
        level=str(obs_cfg.get("log_level", "INFO")),
        log_dir=obs_cfg.get("log_dir"),
        json=obs_cfg.get("log_json"),
    )
    if bool(obs_cfg.get("metrics_enabled", False)):
        start_metrics_server(
            port=int(obs_cfg.get("metrics_port_thermal", 9101)),
            addr=str(obs_cfg.get("metrics_bind", "0.0.0.0")),
        )

    thermal_cfg = ThermalConfig.from_cfg(cfg, cfg_dir)
    if not thermal_cfg.enabled:
        print("[thermal] thermal.enabled is false; nothing to do", file=sys.stderr)
        return 0
    if not thermal_cfg.rooms:
        print("[thermal] no rooms configured", file=sys.stderr)
        return 2

    cams_layout_rel = cfg.get("multi_camera", {}).get("cameras_layout_file")
    cams_layout_path = Path(str(cams_layout_rel))
    if not cams_layout_path.is_absolute():
        cams_layout_path = (cfg_dir / cams_layout_path).resolve()
    fall_cfg = load_fall_config(cams_layout_path)

    floorplan = load_maro_floorplan(
        thermal_cfg.maro_api_base,
        Path(thermal_cfg.maro_cache_dir),
    )
    mm_to_plan = make_mm_to_plan_px(floorplan)

    runners: dict[str, RoomFallRunner] = {}
    for spec in thermal_cfg.rooms:
        poly_px = _polygon_plan_px(spec.polygon_mm, mm_to_plan)
        runners[spec.room] = RoomFallRunner(
            room_spec=spec,
            thermal_cfg=thermal_cfg,
            fall_cfg=fall_cfg,
            mm_to_plan_px=mm_to_plan,
            polygon_plan_px=poly_px,
        )

    print(
        f"[thermal] active rooms={list(runners)} broker={thermal_cfg.mqtt_host}:{thermal_cfg.mqtt_port}",
        file=sys.stderr,
    )

    fall_poster = FallEventPoster(thermal_cfg)
    pos_poster: ThermalPositionPoster | None = None
    if thermal_cfg.positions_enabled:
        pos_poster = ThermalPositionPoster(
            thermal_cfg,
            plan_w_px=floorplan.plan_width,
            plan_h_px=floorplan.plan_height,
        )
    presence_pub = PresenceMqttPublisher(thermal_cfg)
    presence_pub.start()

    print(
        f"[thermal] POST events to {thermal_cfg.events_url} "
        f"positions={thermal_cfg.positions_url} dry_run={thermal_cfg.poster_dry_run}",
        file=sys.stderr,
    )

    room_online: dict[str, bool] = {spec.room: False for spec in thermal_cfg.rooms}
    last_heartbeat_ts: dict[str, float] = {spec.room: 0.0 for spec in thermal_cfg.rooms}
    runner_lock = threading.Lock()

    def on_thermal(msg: ThermalFrameMessage) -> None:
        with runner_lock:
            runner = runners.get(msg.room)
            if runner is None:
                return
            result = runner.feed_frame(msg.temp_c, msg.ts)

        metrics.thermal_blob_area.labels(room=msg.room).set(float(result.blob_area))

        if result.plan_px is not None:
            metrics.thermal_centroid_plan_px.labels(room=msg.room, axis="x").set(
                float(result.plan_px[0])
            )
            metrics.thermal_centroid_plan_px.labels(room=msg.room, axis="y").set(
                float(result.plan_px[1])
            )

        presence_pub.publish(msg.room, in_room=result.in_room, ts=msg.ts)

        if pos_poster is not None and result.should_post_position and result.plan_px:
            ok, err = pos_poster.post(msg.room, msg.ts, result.plan_px)
            if ok:
                metrics.thermal_position_posts.labels(room=msg.room).inc()
            else:
                metrics.post_failures.labels(endpoint="positions").inc()
                print(f"[thermal] position POST failed: {err}", file=sys.stderr)

        for a in result.alarms:
            ok, err = fall_poster.emit(a, water_leak=result.leak_active)
            metrics.thermal_fall_events.labels(room=a.room, confidence=a.confidence).inc()
            if not ok:
                metrics.post_failures.labels(endpoint="events").inc()
                print(f"[thermal] POST failed: {err}", file=sys.stderr)
            else:
                metrics.thermal_last_event_age.labels(room=a.room).set(0.0)
                print(
                    f"[thermal] fall event posted: room={a.room} conf={a.confidence} reason={a.reason}",
                    file=sys.stderr,
                )

    def on_leak(msg: LeakStateMessage) -> None:
        with runner_lock:
            r = runners.get(msg.room)
            if r is not None:
                r.set_leak(msg.wet)
        print(f"[thermal] leak update room={msg.room} wet={msg.wet}", file=sys.stderr)

    def on_door(msg: DoorStateMessage) -> None:
        with runner_lock:
            r = runners.get(msg.room)
            if r is not None:
                r.set_door(msg.open)
        print(f"[thermal] door room={msg.room} open={msg.open}", file=sys.stderr)

    def on_heartbeat(msg: HeartbeatMessage) -> None:
        room_online[msg.room] = msg.online
        last_heartbeat_ts[msg.room] = time.time()
        metrics.thermal_room_online.labels(room=msg.room).set(1.0 if msg.online else 0.0)
        print(f"[thermal] heartbeat room={msg.room} online={msg.online}", file=sys.stderr)

    sub = MqttSubscriber(
        thermal_cfg,
        on_thermal=on_thermal,
        on_leak=on_leak,
        on_door=on_door,
        on_heartbeat=on_heartbeat,
    )
    sub.start()

    stop_event = threading.Event()

    def _handle_sig(_signum, _frame) -> None:
        print("[thermal] signal received; stopping", file=sys.stderr)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    def _watchdog() -> None:
        while not stop_event.wait(15.0):
            now = time.time()
            for room, ts in last_heartbeat_ts.items():
                if ts <= 0:
                    continue
                age = now - ts
                if age > 30.0 and room_online.get(room, False):
                    print(
                        f"[thermal] WARN: no heartbeat from {room} for {age:.0f}s",
                        file=sys.stderr,
                    )
                    room_online[room] = False
                    metrics.thermal_room_online.labels(room=room).set(0.0)

    watchdog = threading.Thread(target=_watchdog, name="thermal-heartbeat-watch", daemon=True)
    watchdog.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        sub.stop()
        presence_pub.stop()
        fall_poster.close()
        if pos_poster is not None:
            pos_poster.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("tracking_engine/config.multi_camera.yaml"),
    )
    args = ap.parse_args()
    if not args.config.is_file():
        print(f"[thermal] missing config: {args.config}", file=sys.stderr)
        return 2
    return run(args.config.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
