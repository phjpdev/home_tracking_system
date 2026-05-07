"""YOLOv8n person detection on CPU (Ultralytics)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    import supervision as sv

PERSON_CLASS_ID = 0


class UltralyticsCpuDetector:
    def __init__(self, weights: str, conf: float):
        from ultralytics import YOLO

        self._model = YOLO(weights)
        self._conf = conf

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
            verbose=False,
        )[0]
        detections = sv.Detections.from_ultralytics(results)
        if latency_record:
            latency_record((time.perf_counter() - t0) * 1000.0)
        return detections

    def backend(self) -> str:
        return "cpu_ultralytics"
