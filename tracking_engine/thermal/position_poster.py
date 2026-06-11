"""POST thermal presence positions to Maro ``/tracking/positions``."""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..pipeline.poster import PositionPoster
from .config import ThermalConfig


class ThermalPositionPoster:
    """Build and POST privacy-room position payloads (plan pixels on the wire)."""

    def __init__(
        self,
        cfg: ThermalConfig,
        *,
        plan_w_px: int,
        plan_h_px: int,
    ):
        self._cfg = cfg
        self._poster = PositionPoster(
            url=cfg.positions_url,
            timeout=cfg.poster_timeout_sec,
            dry_run=cfg.poster_dry_run,
            verify_tls=cfg.verify_tls,
            plan_w_px=plan_w_px,
            plan_h_px=plan_h_px,
        )

    def build_payload(
        self,
        room: str,
        ts: float,
        plan_px: tuple[float, float],
    ) -> dict[str, Any]:
        cam_id = f"thermal_{room.lower()}"
        return {
            "cam_id": cam_id,
            "ts": round(float(ts), 3),
            "persons": [
                {
                    "id": cam_id,
                    "x": round(plan_px[0], 1),
                    "y": round(plan_px[1], 1),
                    "zone": room,
                    "privacy": True,
                    "position_source": "thermal",
                    "cal_confidence": "low",
                }
            ],
        }

    def post(
        self,
        room: str,
        ts: float,
        plan_px: tuple[float, float],
    ) -> tuple[bool, str]:
        if not self._cfg.positions_enabled:
            return True, ""
        payload = self.build_payload(room, ts, plan_px)
        return self._poster.post(payload)

    def close(self) -> None:
        self._poster.close()


def make_mm_to_plan_px(
    assets: Any,
) -> Callable[[float, float], tuple[float, float]]:
    """Return a callable wrapping :meth:`MaroFloorplanAssets.mm_to_plan_px`."""

    def _convert(x_mm: float, y_mm: float) -> tuple[float, float]:
        return assets.mm_to_plan_px(x_mm, y_mm)

    return _convert
