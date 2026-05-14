"""paho-mqtt subscriber that decodes thermal frames + door/leak events."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import ThermalConfig

LOG = logging.getLogger("tracking_engine.thermal.mqtt")


@dataclass
class ThermalFrameMessage:
    room: str
    ts: float
    temp_c: list[float]
    shape: tuple[int, int]


@dataclass
class LeakStateMessage:
    room: str
    wet: bool


@dataclass
class DoorStateMessage:
    room: str
    open: bool


class MqttSubscriber:
    """Threaded paho-mqtt client. Decodes payloads and dispatches callbacks."""

    def __init__(
        self,
        cfg: ThermalConfig,
        *,
        on_thermal: Callable[[ThermalFrameMessage], None],
        on_leak: Optional[Callable[[LeakStateMessage], None]] = None,
        on_door: Optional[Callable[[DoorStateMessage], None]] = None,
    ):
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "paho-mqtt is required: pip install paho-mqtt"
            ) from exc

        self._cfg = cfg
        self._on_thermal = on_thermal
        self._on_leak = on_leak
        self._on_door = on_door
        self._mqtt = mqtt
        self._client = mqtt.Client(
            client_id=f"tracking-thermal-{int(time.time())}",
            clean_session=True,
        )
        if cfg.mqtt_user:
            self._client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password or "")
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._stop = threading.Event()

    def start(self) -> None:
        try:
            self._client.connect(self._cfg.mqtt_host, self._cfg.mqtt_port, keepalive=30)
        except OSError as exc:
            print(f"[thermal-mqtt] connect failed: {exc}", file=sys.stderr)
            raise
        self._client.loop_start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._client.loop_stop()
        except Exception:
            pass
        try:
            self._client.disconnect()
        except Exception:
            pass

    def _on_connect(self, client, _userdata, _flags, rc, *args) -> None:
        if rc != 0:
            print(f"[thermal-mqtt] MQTT connect rc={rc}", file=sys.stderr)
            return
        prefix = self._cfg.topic_prefix
        for room in {r.room.lower() for r in self._cfg.rooms}:
            client.subscribe(f"{prefix}/{room}/thermal/frame", qos=0)
            client.subscribe(f"{prefix}/{room}/door/state", qos=1)
            client.subscribe(f"{prefix}/{room}/leak/state", qos=1)

    def _on_disconnect(self, _client, _userdata, rc, *args) -> None:
        print(f"[thermal-mqtt] disconnected rc={rc}; auto-reconnect by loop", file=sys.stderr)

    def _on_message(self, _client, _userdata, msg) -> None:
        topic = msg.topic
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            return

        parts = topic.split("/")
        # home/<room>/<kind>/<sub>
        if len(parts) < 4:
            return
        room = parts[1].upper()
        kind = parts[2]
        if kind == "thermal" and parts[3] == "frame":
            self._dispatch_thermal(room, payload)
        elif kind == "door" and parts[3] == "state" and self._on_door is not None:
            self._on_door(DoorStateMessage(room=room, open=(payload.strip().lower() == "open")))
        elif kind == "leak" and parts[3] == "state" and self._on_leak is not None:
            self._on_leak(LeakStateMessage(room=room, wet=(payload.strip().lower() == "wet")))

    def _dispatch_thermal(self, room: str, payload: str) -> None:
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            return
        try:
            ts = float(data["ts"])
            shape = tuple(int(v) for v in data.get("shape", (24, 32)))
            temps = [float(v) for v in data["temp_c"]]
        except (KeyError, TypeError, ValueError):
            return
        if len(shape) != 2 or shape[0] * shape[1] != len(temps):
            return
        self._on_thermal(
            ThermalFrameMessage(
                room=room,
                ts=ts,
                temp_c=temps,
                shape=(shape[0], shape[1]),
            )
        )
