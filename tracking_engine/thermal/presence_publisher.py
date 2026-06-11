"""MQTT publisher for thermal room presence (optical–thermal fusion)."""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Optional

from .config import ThermalConfig


class PresenceMqttPublisher:
    """Lightweight MQTT client for ``home/<room>/thermal/presence`` topics."""

    def __init__(self, cfg: ThermalConfig):
        self._cfg = cfg
        self._client = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self._cfg.mqtt_publish_presence:
            return
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is required: pip install paho-mqtt") from exc

        client = mqtt.Client(
            client_id=f"tracking-thermal-pub-{int(time.time())}",
            clean_session=True,
        )
        if self._cfg.mqtt_user:
            client.username_pw_set(self._cfg.mqtt_user, self._cfg.mqtt_password or "")
        try:
            client.connect(self._cfg.mqtt_host, self._cfg.mqtt_port, keepalive=30)
        except OSError as exc:
            print(f"[thermal-mqtt-pub] connect failed: {exc}", file=sys.stderr)
            raise
        client.loop_start()
        self._client = client

    def publish(self, room: str, *, in_room: bool, ts: float) -> None:
        if not self._cfg.mqtt_publish_presence or self._client is None:
            return
        topic = f"{self._cfg.topic_prefix}/{room.lower()}/thermal/presence"
        payload = json.dumps(
            {
                "in_room": bool(in_room),
                "zone": room,
                "ts": round(float(ts), 3),
            }
        )
        with self._lock:
            self._client.publish(topic, payload, qos=0, retain=True)

    def stop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.loop_stop()
        except Exception:
            pass
        try:
            self._client.disconnect()
        except Exception:
            pass
        self._client = None
