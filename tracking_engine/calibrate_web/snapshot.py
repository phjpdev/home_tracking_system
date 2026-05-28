"""Background snapshot pool for the calibration UI.

Wraps one :class:`tracking_engine.pipeline.ingest.LatestRtspSource` per
active camera (or a one-shot ``VideoCapture`` opener for ``--video``
overrides used during laptop replay), applies the per-camera rotate
override the runtime already honours, and exposes the latest JPEG-encoded
frame for the FastAPI snapshot endpoint.

The pool is intentionally simple: each camera owns a daemon thread that
keeps only the most recent decoded frame, so "Recapture all" from the
browser just returns whatever was last decoded. No backpressure, no
queueing.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from ..pipeline.camera_undistort import CameraUndistortRegistry
from ..pipeline.ingest import LatestRtspSource, open_video


_ALLOWED_ROTATIONS = (0, 90, 180, 270)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def _apply_rotate(frame: Optional[np.ndarray], deg: int) -> Optional[np.ndarray]:
    if frame is None or deg == 0:
        return frame
    if deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


class _CameraSlot:
    """One camera: thread-managed source + cached oriented frame + last size."""

    def __init__(
        self,
        cam: dict[str, Any],
        undistort: Optional[CameraUndistortRegistry] = None,
    ):
        self.name: str = str(cam["name"])
        self._undistort = undistort
        self.rotate: int = int(cam.get("rotate") or 0)
        if self.rotate not in _ALLOWED_ROTATIONS:
            self.rotate = 0
        self.rtsp_url: str = str(cam.get("rtsp_url") or "").strip()
        self.video_path: Optional[str] = cam.get("video_path") or None

        self._lock = threading.Lock()
        self._oriented: Optional[np.ndarray] = None
        self._size: Optional[tuple[int, int]] = None
        self._error: Optional[str] = None
        self._source: Optional[LatestRtspSource] = None
        self._video_cap: Optional[cv2.VideoCapture] = None
        self._static_image: Optional[np.ndarray] = None
        self._open()

    def _open(self) -> None:
        try:
            if self.video_path:
                vp = Path(self.video_path).expanduser()
                if not vp.is_file():
                    raise FileNotFoundError(f"file not found: {vp}")
                if vp.suffix.lower() in _IMAGE_EXTS:
                    img = cv2.imread(str(vp))
                    if img is None:
                        raise OSError(f"cv2.imread returned None for {vp}")
                    self._static_image = img
                else:
                    self._video_cap = open_video(str(vp)).cap
            elif self.rtsp_url:
                self._source = LatestRtspSource(self.rtsp_url)
            else:
                self._error = "no rtsp_url or video_path configured"
        except (OSError, RuntimeError, ValueError) as exc:
            self._error = f"open failed: {exc}"

    def refresh(self, settle_seconds: float = 0.0) -> dict[str, Any]:
        """Pull the freshest frame from the source, orient it, cache it.

        Returns a small status dict (no frame data) suitable for JSON.
        """

        if self._error:
            return {"ok": False, "error": self._error}

        frame: Optional[np.ndarray] = None
        if self._static_image is not None:
            frame = self._static_image
        elif self._video_cap is not None:
            ok, frame = self._video_cap.read()
            if not ok or frame is None:
                self._video_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._video_cap.read()
            if not ok or frame is None:
                return {"ok": False, "error": "video read failed"}
        else:
            assert self._source is not None
            if settle_seconds > 0:
                time.sleep(settle_seconds)
            ok, frame = self._source.read()
            if not ok or frame is None:
                return {"ok": False, "error": "no decoded frame yet"}

        skip_rotate = self._static_image is not None
        oriented = frame if skip_rotate else _apply_rotate(frame, self.rotate)
        if oriented is None:
            return {"ok": False, "error": "rotate failed"}
        if self._undistort is not None and self._undistort.has(self.name):
            oriented = self._undistort.apply(self.name, oriented)
        h, w = oriented.shape[:2]
        with self._lock:
            self._oriented = oriented
            self._size = (int(w), int(h))
        return {"ok": True, "w": int(w), "h": int(h)}

    def latest_oriented(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._oriented is None else self._oriented.copy()

    def size(self) -> Optional[tuple[int, int]]:
        with self._lock:
            return self._size

    def release(self) -> None:
        if self._source is not None:
            try:
                self._source.release()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
            self._source = None
        if self._video_cap is not None:
            try:
                self._video_cap.release()
            except Exception:  # pragma: no cover
                pass
            self._video_cap = None


class CameraSnapshotPool:
    """Holds one :class:`_CameraSlot` per active camera.

    Construction returns immediately; background RTSP threads start to
    fill in frames asynchronously. Endpoints that need a guaranteed-fresh
    frame should call :meth:`refresh_all` first.
    """

    def __init__(
        self,
        cameras: list[dict[str, Any]],
        undistort: Optional[CameraUndistortRegistry] = None,
    ):
        self._slots: dict[str, _CameraSlot] = {}
        for cam in cameras:
            self._slots[str(cam["name"])] = _CameraSlot(cam, undistort=undistort)

    def names(self) -> list[str]:
        return list(self._slots.keys())

    def refresh_all(self, settle_seconds: float = 0.0) -> dict[str, dict[str, Any]]:
        """Refresh every camera concurrently (best-effort), return per-cam status."""

        results: dict[str, dict[str, Any]] = {}
        threads: list[threading.Thread] = []

        def _worker(name: str, slot: _CameraSlot) -> None:
            results[name] = slot.refresh(settle_seconds=settle_seconds)

        for name, slot in self._slots.items():
            t = threading.Thread(
                target=_worker, args=(name, slot), name=f"snap:{name}", daemon=True
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        for name in self._slots:
            results.setdefault(name, {"ok": False, "error": "refresh timed out"})
        return results

    def get_latest_oriented(self, cam_name: str) -> Optional[np.ndarray]:
        slot = self._slots.get(cam_name)
        if slot is None:
            return None
        frame = slot.latest_oriented()
        if frame is None:
            slot.refresh()
            frame = slot.latest_oriented()
        return frame

    def get_latest_jpeg(self, cam_name: str, quality: int = 85) -> Optional[bytes]:
        frame = self.get_latest_oriented(cam_name)
        if frame is None:
            return None
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
        )
        if not ok:
            return None
        return buf.tobytes()

    def last_size(self, cam_name: str) -> Optional[tuple[int, int]]:
        slot = self._slots.get(cam_name)
        return None if slot is None else slot.size()

    def release_all(self) -> None:
        for slot in self._slots.values():
            slot.release()
        self._slots.clear()
