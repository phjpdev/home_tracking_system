"""Video sources: RTSP (OpenCV) or file — tuned for low buffer latency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import cv2
import numpy as np


class FrameSource(Protocol):
    """Readable video source returning BGR uint8 frames."""

    def read(self) -> tuple[bool, Optional[np.ndarray]]: ...
    def release(self) -> None: ...


@dataclass
class OpenCvSource:
    cap: cv2.VideoCapture

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        return self.cap.read()

    def release(self) -> None:
        self.cap.release()


def _configure_capture(cap: cv2.VideoCapture, rtsp: bool) -> None:
    # Keep decode queue shallow so we don't process stale frames.
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    if rtsp:
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"H264"))
        except Exception:
            pass


def open_rtsp(url: str) -> OpenCvSource:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    _configure_capture(cap, rtsp=True)
    return OpenCvSource(cap)


def open_video(path: str) -> OpenCvSource:
    cap = cv2.VideoCapture(path)
    _configure_capture(cap, rtsp=False)
    return OpenCvSource(cap)
