"""Per-camera lens undistortion (optional K, dist from calibration JSON)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CameraUndistortRegistry:
    """Loads ``camera_intrinsics.json`` and undistorts frames per camera id."""

    def __init__(self, entries: dict[str, dict[str, Any]]):
        self._entries = entries
        self._maps: dict[str, tuple[np.ndarray, np.ndarray, tuple[int, int]]] = {}

    @classmethod
    def from_path(cls, path: Path) -> CameraUndistortRegistry:
        if not path.is_file():
            return cls({})
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {k: v for k, v in data.items() if not str(k).startswith("_")}
        reg = cls(entries)
        if entries:
            logger.info(
                "camera_undistort: loaded intrinsics for %s",
                ", ".join(sorted(entries.keys())),
            )
        return reg

    def has(self, cam_id: str) -> bool:
        return cam_id in self._entries

    def _ensure_maps(self, cam_id: str, frame_w: int, frame_h: int) -> None:
        key = f"{cam_id}:{frame_w}x{frame_h}"
        if key in self._maps:
            return
        entry = self._entries[cam_id]
        K = np.array(entry["K"], dtype=np.float64)
        dist = np.array(entry["dist"], dtype=np.float64).reshape(-1, 1)
        new_k, _roi = cv2.getOptimalNewCameraMatrix(
            K, dist, (frame_w, frame_h), alpha=float(entry.get("alpha", 0.0)), newImgSize=(frame_w, frame_h)
        )
        map1, map2 = cv2.initUndistortRectifyMap(
            K, dist, None, new_k, (frame_w, frame_h), cv2.CV_16SC2
        )
        self._maps[key] = (map1, map2, (frame_w, frame_h))

    def apply(self, cam_id: str, frame_bgr: np.ndarray) -> np.ndarray:
        if cam_id not in self._entries or frame_bgr is None:
            return frame_bgr
        h, w = frame_bgr.shape[:2]
        self._ensure_maps(cam_id, w, h)
        key = f"{cam_id}:{w}x{h}"
        map1, map2, _ = self._maps[key]
        return cv2.remap(frame_bgr, map1, map2, interpolation=cv2.INTER_LINEAR)
