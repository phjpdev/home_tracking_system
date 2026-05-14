"""Thermal pipeline configuration loaded from YAML + cameras_config.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class RoomThermalSpec:
    room: str                                # "SZ" | "BZ"
    mount_xyz_mm: tuple[int, int, int]       # ceiling mount position
    fov_h_deg: float                         # MLX90640 horizontal FOV
    fov_v_deg: float                         # MLX90640 vertical FOV
    frame_shape: tuple[int, int]             # (rows, cols) = (24, 32)
    polygon_mm: list[tuple[int, int]]        # privacy_thermal_polygon_mm
    has_leak_probe: bool


@dataclass(frozen=True)
class ThermalConfig:
    enabled: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_user: Optional[str]
    mqtt_password: Optional[str]
    topic_prefix: str
    poster_url: str
    poster_timeout_sec: float
    poster_dry_run: bool
    verify_tls: bool
    event_throttle_sec: float
    body_temp_min_c: float
    body_temp_max_c: float
    motion_threshold_normalized: float
    background_alpha: float
    rooms: list[RoomThermalSpec] = field(default_factory=list)

    @staticmethod
    def from_cfg(cfg: dict[str, Any], cfg_dir: Path) -> "ThermalConfig":
        t = cfg.get("thermal") or {}
        if not isinstance(t, dict):
            t = {}
        enabled = bool(t.get("enabled", False))

        mqtt = t.get("mqtt") or {}
        if not isinstance(mqtt, dict):
            mqtt = {}
        mqtt_host = str(mqtt.get("host", "127.0.0.1"))
        mqtt_port = int(mqtt.get("port", 1883))
        mqtt_user = mqtt.get("user")
        mqtt_user = str(mqtt_user) if mqtt_user is not None else None
        mqtt_password = mqtt.get("password")
        mqtt_password = str(mqtt_password) if mqtt_password is not None else None
        topic_prefix = str(mqtt.get("topic_prefix", "home"))

        # Re-use the same Maro poster contract as multi_camera.py unless overridden.
        pcfg = cfg.get("poster") or {}
        if not isinstance(pcfg, dict):
            pcfg = {}
        events_url = t.get("events_url") or pcfg.get("events_url") or pcfg.get("url")
        if not events_url:
            raise RuntimeError(
                "thermal pipeline needs an events POST url. "
                "Set thermal.events_url or poster.events_url / poster.url."
            )
        poster_url = str(events_url)
        poster_timeout_sec = float(pcfg.get("timeout_sec", 3.0))
        poster_dry_run = bool(t.get("dry_run", pcfg.get("dry_run", False)))
        verify_tls = bool(pcfg.get("verify_tls", True))

        det = t.get("detection") or {}
        if not isinstance(det, dict):
            det = {}
        event_throttle_sec = float(det.get("event_throttle_sec", 30.0))
        body_temp_min_c = float(det.get("body_temp_min_c", 28.0))
        body_temp_max_c = float(det.get("body_temp_max_c", 38.5))
        motion_threshold_normalized = float(det.get("motion_threshold_normalized", 0.02))
        background_alpha = float(det.get("background_alpha", 0.02))

        cams_layout_file = cfg.get("multi_camera", {}).get("cameras_layout_file")
        if not cams_layout_file:
            raise RuntimeError(
                "thermal pipeline reads room geometry from multi_camera.cameras_layout_file; "
                "set it in the same config."
            )
        layout_path = Path(str(cams_layout_file))
        if not layout_path.is_absolute():
            layout_path = (cfg_dir / layout_path).resolve()
        cams_data: dict[str, Any] = json.loads(layout_path.read_text(encoding="utf-8"))

        rooms_block = t.get("rooms")
        if rooms_block is None:
            rooms_block = ["SZ", "BZ"]
        if not isinstance(rooms_block, list):
            raise RuntimeError("thermal.rooms must be a list of room codes")

        sensor_block = cams_data.get("privacy_room_sensors") or {}
        zones_block = cams_data.get("privacy_thermal_zones_mm") or {}
        mounts_block = cams_data.get("privacy_hardware_mounts_mm") or {}

        room_specs: list[RoomThermalSpec] = []
        for room in rooms_block:
            room = str(room).upper()
            poly_raw = zones_block.get(room)
            if not poly_raw:
                raise RuntimeError(f"privacy_thermal_zones_mm missing for {room}")
            polygon = [(int(p[0]), int(p[1])) for p in poly_raw]

            mount_raw = (mounts_block.get(room) or {}).get("thermal_ceiling_mount_mm")
            if not mount_raw:
                raise RuntimeError(f"privacy_hardware_mounts_mm missing thermal mount for {room}")
            mount_xyz = (
                int(mount_raw["x_mm"]),
                int(mount_raw["y_mm"]),
                int(mount_raw["z_mm"]),
            )

            srow = sensor_block.get(room) or {}
            fov_h = float(srow.get("fov_h_deg", 55.0))
            fov_v = float(srow.get("fov_v_deg", 35.0))

            has_leak = room == "BZ"
            room_specs.append(
                RoomThermalSpec(
                    room=room,
                    mount_xyz_mm=mount_xyz,
                    fov_h_deg=fov_h,
                    fov_v_deg=fov_v,
                    frame_shape=(24, 32),
                    polygon_mm=polygon,
                    has_leak_probe=has_leak,
                )
            )

        return ThermalConfig(
            enabled=enabled,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
            topic_prefix=topic_prefix,
            poster_url=poster_url,
            poster_timeout_sec=poster_timeout_sec,
            poster_dry_run=poster_dry_run,
            verify_tls=verify_tls,
            event_throttle_sec=event_throttle_sec,
            body_temp_min_c=body_temp_min_c,
            body_temp_max_c=body_temp_max_c,
            motion_threshold_normalized=motion_threshold_normalized,
            background_alpha=background_alpha,
            rooms=room_specs,
        )
