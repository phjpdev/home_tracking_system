"""Convert FallAlarm -> JSON event -> HTTP POST to Maro."""

from __future__ import annotations

import json
from typing import Any

from camera_placement_plan.thermal_fall_detection import FallAlarm

from ..pipeline.poster import PositionPoster
from .config import ThermalConfig


class FallEventPoster:
    def __init__(self, cfg: ThermalConfig):
        self._poster = PositionPoster(
            url=cfg.events_url,
            timeout=cfg.poster_timeout_sec,
            dry_run=cfg.poster_dry_run,
            verify_tls=cfg.verify_tls,
        )

    def emit(self, alarm: FallAlarm, *, water_leak: bool = False) -> tuple[bool, str]:
        payload: dict[str, Any] = {
            "event_type": "fall",
            "room": alarm.room,
            "ts": round(float(alarm.t_sec), 3),
            "confidence": alarm.confidence,
            "reason": alarm.reason,
            "source": "thermal_mlx90640",
        }
        if water_leak:
            payload["water_leak"] = True
        return self._poster.post(payload)

    def close(self) -> None:
        self._poster.close()


def encode_event(alarm: FallAlarm, *, water_leak: bool = False) -> str:
    payload: dict[str, Any] = {
        "event_type": "fall",
        "room": alarm.room,
        "ts": round(float(alarm.t_sec), 3),
        "confidence": alarm.confidence,
        "reason": alarm.reason,
        "source": "thermal_mlx90640",
    }
    if water_leak:
        payload["water_leak"] = True
    return json.dumps(payload)
