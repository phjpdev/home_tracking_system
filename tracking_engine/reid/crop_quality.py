"""Person crop extraction and quality gating."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class CropQualityResult:
    ok: bool
    quality_score: float
    bbox_area: float
    blur_var: float
    aspect: float


def person_crop(frame: np.ndarray, xyxy: tuple[float, float, float, float]) -> Optional[np.ndarray]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = xyxy
    ix1 = max(0, int(np.floor(x1)))
    iy1 = max(0, int(np.floor(y1)))
    ix2 = min(w, int(np.ceil(x2)))
    iy2 = min(h, int(np.ceil(y2)))
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    crop = frame[iy1:iy2, ix1:ix2]
    if crop.size == 0:
        return None
    return crop


def assess_crop_quality(
    crop: np.ndarray,
    *,
    min_bbox_area: float,
    min_blur_var: float,
    min_aspect: float,
    max_aspect: float,
) -> CropQualityResult:
    h, w = crop.shape[:2]
    area = float(max(h * w, 1))
    aspect = float(h) / float(max(w, 1))

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    ok_area = area >= min_bbox_area
    ok_blur = blur_var >= min_blur_var
    ok_aspect = min_aspect <= aspect <= max_aspect
    ok = ok_area and ok_blur and ok_aspect

    # Heuristic composite in [0,1] — only used for gallery learning thresholds.
    a = min(area / max(min_bbox_area, 1.0), 6.0) / 6.0
    b = min(blur_var / max(min_blur_var, 1.0), 4.0) / 4.0
    c = 1.0 if ok_aspect else 0.35
    quality_score = float(max(0.0, min(1.0, 0.4 * a + 0.35 * b + 0.25 * c)))

    return CropQualityResult(
        ok=ok,
        quality_score=quality_score,
        bbox_area=area,
        blur_var=blur_var,
        aspect=aspect,
    )


def clipped_head_bbox(
    full_xyxy: tuple[float, float, float, float],
    fh: int,
    fw: int,
) -> Tuple[int, int, int, int]:
    """Rough head region = upper ~35%% of bbox (for optional future face path)."""
    x1, y1, x2, y2 = full_xyxy
    h = max(y2 - y1, 1.0)
    y2h = y1 + 0.35 * h
    ix1 = max(0, int(np.floor(x1)))
    iy1 = max(0, int(np.floor(y1)))
    ix2 = min(fw, int(np.ceil(x2)))
    iy2 = min(fh, int(np.ceil(y2h)))
    return ix1, iy1, ix2, iy2
