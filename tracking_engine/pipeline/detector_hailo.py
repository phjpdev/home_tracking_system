"""YOLO detection on Hailo via Picamera2's Hailo wrapper (RGB frame in, boxes out).

Works on Raspberry Pi OS with ``picamera2`` + Hailo packages installed. Input frames
are resized to the model's expected resolution internally.

For RTSP ingest the camera path is OpenCV; only the inference step uses Hailo.
If ``picamera2`` or the HEF file is unavailable, use ``backend: cpu`` in config.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

DEFAULT_CLASS_ID = 0  # COCO person; 56 = chair (floor-plan test proxy)


def _extract_yolo_detections(
    hailo_output,
    frame_w: int,
    frame_h: int,
    class_id: int,
    conf: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert Hailo postprocess output to xyxy / confidence / class_id arrays."""
    xyxy_list: List[List[float]] = []
    conf_list: List[float] = []
    cls_list: List[int] = []
    if not hailo_output:
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )
    if class_id >= len(hailo_output):
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )
    for detection in hailo_output[class_id]:
        score = float(detection[4])
        if score < conf:
            continue
        y0, x0, y1, x1 = detection[:4]
        x1a = int(x0 * frame_w)
        y1a = int(y0 * frame_h)
        x2a = int(x1 * frame_w)
        y2a = int(y1 * frame_h)
        xyxy_list.append([x1a, y1a, x2a, y2a])
        conf_list.append(score)
        cls_list.append(class_id)
    if not xyxy_list:
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int32),
        )
    return (
        np.array(xyxy_list, dtype=np.float32),
        np.array(conf_list, dtype=np.float32),
        np.array(cls_list, dtype=np.int32),
    )


class HailoPicamera2Detector:
    def __init__(self, hef_path: str, conf: float, class_id: int = DEFAULT_CLASS_ID):
        from picamera2.devices import Hailo

        self._hailo = Hailo(hef_path)
        self._hailo.__enter__()
        self._model_h, self._model_w, _ = self._hailo.get_input_shape()
        self._conf = conf
        self._class_id = int(class_id)

    def close(self) -> None:
        try:
            self._hailo.__exit__(None, None, None)
        except Exception:
            pass

    def detect(self, frame: np.ndarray, latency_record: Optional[Callable[[float], None]] = None):
        import supervision as sv

        t0 = time.perf_counter()
        oh, ow = frame.shape[:2]
        resized = cv2.resize(frame, (self._model_w, self._model_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        results = self._hailo.run(rgb)
        xyxy_s, conf, cls = _extract_yolo_detections(
            results, self._model_w, self._model_h, self._class_id, self._conf
        )
        if len(xyxy_s) > 0:
            sx = ow / self._model_w
            sy = oh / self._model_h
            xyxy_s[:, 0] *= sx
            xyxy_s[:, 2] *= sx
            xyxy_s[:, 1] *= sy
            xyxy_s[:, 3] *= sy
        det = sv.Detections(xyxy=xyxy_s, confidence=conf, class_id=cls)
        if latency_record:
            latency_record((time.perf_counter() - t0) * 1000.0)
        return det

    def backend(self) -> str:
        return "hailo_picamera2"


def hailo_detector_available() -> bool:
    try:
        import picamera2.devices  # noqa: F401

        return True
    except Exception:
        return False
