"""Suppress optical floor-plan dots when thermal confirms privacy-room occupancy."""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..thermal.geometry import point_in_polygon


@dataclass(frozen=True)
class ThermalGateConfig:
    enabled: bool = False
    boundary_buffer_px: float = 80.0
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_user: Optional[str] = None
    mqtt_password: Optional[str] = None
    topic_prefix: str = "home"
    rooms: tuple[str, ...] = ("SZ", "BZ")


class ThermalPresenceTracker:
    """Background MQTT subscriber for ``home/<room>/thermal/presence`` (retained)."""

    def __init__(self, cfg: ThermalGateConfig):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._in_room: dict[str, bool] = {r: False for r in cfg.rooms}
        self._client = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self._cfg.enabled:
            return
        self._thread = threading.Thread(target=self._run, name="thermal-gate-mqtt", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            try:
                self._client.loop_stop()
            except Exception:
                pass
            try:
                self._client.disconnect()
            except Exception:
                pass

    def snapshot(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._in_room)

    def _run(self) -> None:
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError:
            print("[thermal-gate] paho-mqtt not installed; gate disabled", file=sys.stderr)
            return

        client = mqtt.Client(client_id=f"thermal-gate-{int(time.time())}", clean_session=True)
        if self._cfg.mqtt_user:
            client.username_pw_set(self._cfg.mqtt_user, self._cfg.mqtt_password or "")

        def _on_connect(c, _u, _f, rc, *args) -> None:
            if rc != 0:
                return
            for room in self._cfg.rooms:
                c.subscribe(
                    f"{self._cfg.topic_prefix}/{room.lower()}/thermal/presence",
                    qos=0,
                )

        def _on_message(_c, _u, msg) -> None:
            parts = msg.topic.split("/")
            if len(parts) < 4:
                return
            room = parts[1].upper()
            if room not in self._in_room:
                return
            try:
                data = json.loads(msg.payload.decode("utf-8"))
                in_room = bool(data.get("in_room", False))
            except (json.JSONDecodeError, TypeError):
                return
            with self._lock:
                self._in_room[room] = in_room

        client.on_connect = _on_connect
        client.on_message = _on_message
        try:
            client.connect(self._cfg.mqtt_host, self._cfg.mqtt_port, keepalive=30)
        except OSError as exc:
            print(f"[thermal-gate] MQTT connect failed: {exc}", file=sys.stderr)
            return
        self._client = client
        client.loop_start()
        while not self._stop.wait(1.0):
            pass
        client.loop_stop()


def _near_polygon(
    x: float,
    y: float,
    poly: list[tuple[float, float]],
    buffer_px: float,
) -> bool:
    if point_in_polygon(x, y, poly):
        return True
    if buffer_px <= 0:
        return False
    # Sample vertices and edge midpoints as a cheap proximity test.
    for px, py in poly:
        if ((x - px) ** 2 + (y - py) ** 2) ** 0.5 <= buffer_px:
            return True
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        mx, my = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
        if ((x - mx) ** 2 + (y - my) ** 2) ** 0.5 <= buffer_px:
            return True
    return False


def apply_thermal_gate(
    persons: list[dict[str, Any]],
    *,
    presence: dict[str, bool],
    privacy_zones_plan_px: dict[str, list[tuple[float, float]]],
    buffer_px: float,
) -> list[dict[str, Any]]:
    """Drop optical persons inside a privacy zone when thermal reports occupancy."""
    if not persons or not any(presence.values()):
        return persons

    out: list[dict[str, Any]] = []
    for p in persons:
        try:
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
        except (TypeError, ValueError):
            out.append(p)
            continue
        zone = str(p.get("zone", ""))
        drop = False
        for room, occupied in presence.items():
            if not occupied:
                continue
            poly = privacy_zones_plan_px.get(room)
            if poly is None:
                if zone.upper() == room:
                    drop = True
                continue
            if zone.upper() == room or _near_polygon(x, y, poly, buffer_px):
                drop = True
                break
        if not drop:
            out.append(p)
    return out


def privacy_zones_mm_to_plan_px(
    zones_mm: dict[str, list],
    mm_to_plan_px: Any,
) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    for room, poly in zones_mm.items():
        if not isinstance(poly, list):
            continue
        pts = []
        for p in poly:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append(mm_to_plan_px(float(p[0]), float(p[1])))
        if pts:
            out[str(room).upper()] = pts
    return out
