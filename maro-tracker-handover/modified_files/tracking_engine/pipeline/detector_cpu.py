"""YOLOv8n person detection — auto-selects MPS (Apple GPU) / CUDA / CPU."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

if TYPE_CHECKING:
    import supervision as sv

logger = logging.getLogger(__name__)
PERSON_CLASS_ID = 0


def _pick_device() -> str:
    """Resolve best available torch device: cuda > mps > cpu. Env override: YOLO_DEVICE."""
    forced = os.environ.get("YOLO_DEVICE", "").strip().lower()
    if forced:
        return forced
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available() and torch.backends.mps.is_built():
            return "mps"
    except Exception:
        pass
    return "cpu"


class UltralyticsCpuDetector:
    def __init__(self, weights: str, conf: float):
        from ultralytics import YOLO

        self._model = YOLO(weights)
        self._conf = conf
        self._device = _pick_device()
        # Move model to the selected device once (avoids per-frame transfer)
        try:
            self._model.to(self._device)
            logger.info("yolo detector: weights=%s device=%s", weights, self._device)
        except Exception as exc:
            logger.warning("yolo detector: .to(%s) failed (%s) — falling back to cpu",
                           self._device, exc)
            self._device = "cpu"

    def detect(
        self,
        frame: np.ndarray,
        latency_record: Optional[Callable[[float], None]] = None,
    ) -> "sv.Detections":
        import supervision as sv

        t0 = time.perf_counter()
        results = self._model(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=self._conf,
            device=self._device,
            verbose=False,
        )[0]
        detections = sv.Detections.from_ultralytics(results)
        if latency_record:
            latency_record((time.perf_counter() - t0) * 1000.0)
        return detections

    def backend(self) -> str:
        return f"ultralytics_{self._device}"
