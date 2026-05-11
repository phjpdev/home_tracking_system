"""Video sources: RTSP (OpenCV) or file — tuned for low buffer latency."""

from __future__ import annotations

import threading
import time
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
    if not cap.isOpened():
        cap.release()
        raise OSError(
            f"could not open video (missing path, permissions, or unsupported codec): {path!r}"
        )
    return OpenCvSource(cap)


class LatestRtspSource:
    """Background thread continuously grabs RTSP frames; ``read()`` returns the freshest copy.

    Drops backlog implicitly by overwriting the latest frame, which keeps latency closer to
    real time when multiple cameras share one inference loop.
    """

    def __init__(self, url: str):
        self._url = url
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._ready = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"rtsp:{url[:48]}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        _configure_capture(cap, rtsp=True)
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    with self._lock:
                        self._frame = frame
                        self._ready = True
                else:
                    time.sleep(0.02)
        finally:
            cap.release()

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        with self._lock:
            if not self._ready or self._frame is None:
                return False, None
            return True, self._frame.copy()

    def release(self) -> None:
        self._stop.set()
        self._thread.join(timeout=8.0)


def open_rtsp_latest(url: str) -> LatestRtspSource:
    return LatestRtspSource(url)
