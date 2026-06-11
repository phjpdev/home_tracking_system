"""YOLOv8n person detection on CPU (Ultralytics)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    import supervision as sv

DEFAULT_CLASS_ID = 0  # COCO person; 56 = chair (floor-plan test proxy)


class UltralyticsCpuDetector:
    def __init__(self, weights: str, conf: float, class_id: int = DEFAULT_CLASS_ID):
        from ultralytics import YOLO

        self._model = YOLO(weights)
        self._conf = conf
        self._class_id = int(class_id)

    def detect(
        self,
        frame: np.ndarray,
        latency_record: Optional[Callable[[float], None]] = None,
    ) -> "sv.Detections":
        import supervision as sv

        t0 = time.perf_counter()
        results = self._model(
            frame,
            classes=[self._class_id],
            conf=self._conf,
            verbose=False,
        )[0]
        detections = sv.Detections.from_ultralytics(results)
        if latency_record:
            latency_record((time.perf_counter() - t0) * 1000.0)
        return detections

    def backend(self) -> str:
        return "cpu_ultralytics"
