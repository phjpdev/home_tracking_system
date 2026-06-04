"""Tests for LED auto-calibration blob detection."""

from __future__ import annotations

import numpy as np

from tracking_engine.calibration.auto_led import detect_bright_centroid


def test_detect_bright_centroid_synthetic() -> None:
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    cv2 = __import__("cv2")
    cv2.circle(frame, (160, 120), 12, (255, 255, 255), -1)
    pt = detect_bright_centroid(frame)
    assert pt is not None
    u, v = pt
    assert abs(u - 160) < 8
    assert abs(v - 120) < 8


def test_detect_bright_centroid_empty() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert detect_bright_centroid(frame) is None
