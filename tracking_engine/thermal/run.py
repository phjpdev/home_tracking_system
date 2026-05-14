#!/usr/bin/env python3
"""Thermal pipeline entrypoint.

Subscribes to MQTT, runs per-room :class:`RoomFallRunner` instances, and POSTs
fall events to Maro. Designed to run as its own systemd unit alongside
``tracking-engine.service`` so a camera-loop hiccup cannot mask a fall event.

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
from .config import ThermalConfig
from .detector_runner import RoomFallRunner, load_fall_config
from .mqtt_subscriber import (
    DoorStateMessage,
    LeakStateMessage,
    MqttSubscriber,
    ThermalFrameMessage,
)
from .poster_bridge import FallEventPoster


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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

    runners: dict[str, RoomFallRunner] = {
        spec.room: RoomFallRunner(room_spec=spec, thermal_cfg=thermal_cfg, fall_cfg=fall_cfg)
        for spec in thermal_cfg.rooms
    }
    print(
        f"[thermal] active rooms={list(runners)} broker={thermal_cfg.mqtt_host}:{thermal_cfg.mqtt_port}",
        file=sys.stderr,
    )
    poster = FallEventPoster(thermal_cfg)
    print(
        f"[thermal] POST events to {thermal_cfg.poster_url} dry_run={thermal_cfg.poster_dry_run}",
        file=sys.stderr,
    )

    runner_lock = threading.Lock()

    def on_thermal(msg: ThermalFrameMessage) -> None:
        with runner_lock:
            runner = runners.get(msg.room)
            if runner is None:
                return
            alarms = runner.feed_frame(msg.temp_c, msg.ts)
        for a in alarms:
            ok, err = poster.emit(a)
            metrics.thermal_fall_events.labels(room=a.room, confidence=a.confidence).inc()
            if not ok:
                metrics.post_failures.labels(endpoint="events").inc()
                print(f"[thermal] POST failed: {err}", file=sys.stderr)
            else:
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
        print(f"[thermal] door room={msg.room} open={msg.open}", file=sys.stderr)

    sub = MqttSubscriber(
        thermal_cfg,
        on_thermal=on_thermal,
        on_leak=on_leak,
        on_door=on_door,
    )
    sub.start()

    stop_event = threading.Event()

    def _handle_sig(_signum, _frame) -> None:
        print("[thermal] signal received; stopping", file=sys.stderr)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        sub.stop()
        poster.close()
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
