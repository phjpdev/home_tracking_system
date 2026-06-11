"""Build CPU or Hailo detector from config."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .detector_cpu import UltralyticsCpuDetector
from .detector_hailo import HailoPicamera2Detector, hailo_detector_available


class PersonDetector(Protocol):
    def detect(self, frame: np.ndarray, latency_record=None): ...
    def backend(self) -> str: ...


def create_detector(cfg: dict[str, Any]) -> tuple[PersonDetector, Any]:
    """Return (detector, cleanup). cleanup may be None or a callable."""

    dcfg = cfg.get("detector", {})
    backend = str(dcfg.get("backend", "auto")).lower()
    conf = float(dcfg.get("conf", 0.4))
    class_id = int(dcfg.get("class_id", 0))  # COCO: 0=person, 56=chair
    hef = str(dcfg.get("hailo_hef", "/usr/share/hailo-models/yolov8s_h8l.hef"))
    weights = str(dcfg.get("yolo_weights", "yolov8n.pt"))

    want_hailo = backend in ("hailo", "auto")
    hef_ok = Path(hef).is_file()

    if want_hailo and backend != "cpu" and hailo_detector_available() and hef_ok:
        det = HailoPicamera2Detector(hef, conf, class_id)
        return det, det.close

    if backend == "hailo":
        raise RuntimeError(
            f"Hailo backend requested but unavailable (picamera2 Hailo or HEF missing: {hef})"
        )

    det = UltralyticsCpuDetector(weights, conf, class_id)
    return det, None
