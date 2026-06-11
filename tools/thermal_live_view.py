#!/usr/bin/env python3
"""Live MLX90640 frame viewer for on-site thermal tuning.

Subscribes to MQTT thermal frames, runs blob extraction, and prints centroid +
plan-pixel projection. Optional OpenCV heatmap when a display is available.

Usage::

    python tools/thermal_live_view.py --host 127.0.0.1 --room sz
    python tools/thermal_live_view.py --host 192.168.178.100 --room bz --body-min 29
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracking_engine.pipeline.maro_floorplan import load_maro_floorplan
from tracking_engine.thermal.blob import analyse_thermal_frame
from tracking_engine.thermal.config import ThermalConfig


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--host", default=None, help="MQTT broker host (overrides config)")
    ap.add_argument("--room", default="sz", help="Room code: sz or bz")
    ap.add_argument("--body-min", type=float, default=None)
    ap.add_argument("--body-max", type=float, default=None)
    ap.add_argument("--alpha", type=float, default=None, help="background_alpha override")
    ap.add_argument("--show", action="store_true", help="OpenCV heatmap window")
    args = ap.parse_args()

    cfg_path = args.config.resolve()
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    thermal_cfg = ThermalConfig.from_cfg(cfg, cfg_path.parent)
    room = args.room.upper()
    spec = next((r for r in thermal_cfg.rooms if r.room == room), None)
    if spec is None:
        print(f"room {room!r} not in thermal.rooms", file=sys.stderr)
        return 2

    body_min = float(args.body_min if args.body_min is not None else thermal_cfg.body_temp_min_c)
    body_max = float(args.body_max if args.body_max is not None else thermal_cfg.body_temp_max_c)
    alpha = float(args.alpha if args.alpha is not None else thermal_cfg.background_alpha)
    host = args.host or thermal_cfg.mqtt_host

    floorplan = load_maro_floorplan(
        thermal_cfg.maro_api_base,
        Path(thermal_cfg.maro_cache_dir),
    )
    poly_px = [floorplan.mm_to_plan_px(float(x), float(y)) for x, y in spec.polygon_mm]

    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError:
        print("pip install paho-mqtt", file=sys.stderr)
        return 2

    background: np.ndarray | None = None
    topic = f"{thermal_cfg.topic_prefix}/{room.lower()}/thermal/frame"

    def on_message(_c, _u, msg) -> None:
        nonlocal background
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            temps = [float(v) for v in data["temp_c"]]
            shape = tuple(int(v) for v in data.get("shape", (24, 32)))
            arr = np.asarray(temps, dtype=np.float32).reshape(shape)
        except (json.JSONDecodeError, KeyError, ValueError):
            return

        mount = (
            spec.mount_xyz_mm[0] + spec.mount_offset_mm[0],
            spec.mount_xyz_mm[1] + spec.mount_offset_mm[1],
            spec.mount_xyz_mm[2],
        )
        blob, background = analyse_thermal_frame(
            frame_c=arr,
            background_c=background,
            background_alpha=alpha,
            body_temp_min_c=body_min,
            body_temp_max_c=body_max,
            frame_shape=spec.frame_shape,
            mount_xyz_mm=mount,
            fov_h_deg=spec.fov_h_deg,
            fov_v_deg=spec.fov_v_deg,
        )
        plan_px = None
        if blob.area_pixels >= 3:
            plan_px = floorplan.mm_to_plan_px(blob.centroid_x_mm, blob.centroid_y_mm)
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] {room} area={blob.area_pixels} posture={blob.posture} "
            f"mm=({blob.centroid_x_mm:.0f},{blob.centroid_y_mm:.0f}) "
            f"plan_px={plan_px} motion={blob.motion_normalized:.3f}",
            flush=True,
        )

        if args.show:
            try:
                import cv2

                norm = cv2.normalize(arr, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
                big = cv2.resize(norm, (320, 240), interpolation=cv2.INTER_NEAREST)
                cv2.imshow(f"thermal_{room.lower()}", big)
                cv2.waitKey(1)
            except Exception:
                pass

    client = mqtt.Client(client_id=f"thermal-live-{int(time.time())}")
    if thermal_cfg.mqtt_user:
        client.username_pw_set(thermal_cfg.mqtt_user, thermal_cfg.mqtt_password or "")
    client.on_message = on_message
    print(f"subscribing {topic} on {host}:{thermal_cfg.mqtt_port}", file=sys.stderr)
    client.connect(host, thermal_cfg.mqtt_port, 60)
    client.subscribe(topic)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
