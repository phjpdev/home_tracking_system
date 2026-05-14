"""SCRFD face detector + ArcFace MobileFaceNet embedder (both ONNX).

Two ONNX models live under ``tracking_engine/models/``:

- ``scrfd_500mf.onnx``    — SCRFD-500MF face detector (insightface model zoo)
- ``arcface_mfn.onnx``    — ArcFace MobileFaceNet, 128-D output

The wrapper exposes a single ``detect_and_embed_head(crop_bgr)`` helper used
by the coordinator's fusion step. When either model is missing or
disabled, calls fall back to ``(None, None, 0.0)`` so the body Re-ID
pipeline continues unchanged.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class FaceDetection:
    bbox_xyxy: tuple[float, float, float, float]   # in crop coordinates
    confidence: float                              # detector score
    frontal_score: float                           # 0..1 heuristic


@dataclass(frozen=True)
class FaceEmbeddingResult:
    detection: FaceDetection
    embedding: np.ndarray   # (dim,), L2-normalised


def _l2_normalize(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x) + 1e-12
    return (x / n).astype(np.float32, copy=False)


class FaceStack:
    """Holds both ONNX sessions; raises only when both are present."""

    def __init__(
        self,
        *,
        detector_onnx_path: str,
        embedder_onnx_path: str,
        dimension: int,
        min_face_size: int,
        min_frontal_score: float,
        det_threshold: float = 0.5,
    ):
        try:
            import onnxruntime as ort  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("onnxruntime is required for FaceStack") from exc

        import onnxruntime as ort

        self._det = ort.InferenceSession(detector_onnx_path, providers=["CPUExecutionProvider"])
        self._emb = ort.InferenceSession(embedder_onnx_path, providers=["CPUExecutionProvider"])
        self._det_input = self._det.get_inputs()[0].name
        self._emb_input = self._emb.get_inputs()[0].name
        self._dimension = int(dimension)
        self._min_face_size = int(min_face_size)
        self._min_frontal_score = float(min_frontal_score)
        self._det_threshold = float(det_threshold)

    @property
    def dimension(self) -> int:
        return self._dimension

    def detect_and_embed_head(
        self,
        head_crop_bgr: np.ndarray,
    ) -> Optional[FaceEmbeddingResult]:
        if head_crop_bgr is None or head_crop_bgr.size == 0:
            return None
        det = self._best_face(head_crop_bgr)
        if det is None:
            return None
        if det.confidence < self._det_threshold:
            return None
        if det.frontal_score < self._min_frontal_score:
            return None
        x1, y1, x2, y2 = det.bbox_xyxy
        if (x2 - x1) < self._min_face_size or (y2 - y1) < self._min_face_size:
            return None
        face_crop = self._extract(head_crop_bgr, det.bbox_xyxy)
        if face_crop is None:
            return None
        vec = self._embed(face_crop)
        if vec.shape[-1] != self._dimension:
            print(
                f"[reid-face] embedder dim {vec.shape[-1]} != configured {self._dimension}",
                file=sys.stderr,
            )
            return None
        return FaceEmbeddingResult(detection=det, embedding=_l2_normalize(vec))

    def _best_face(self, crop_bgr: np.ndarray) -> Optional[FaceDetection]:
        import cv2

        # SCRFD common preprocessing: 640x640 letterbox, RGB, 0..1, BCHW
        h, w = crop_bgr.shape[:2]
        target = 640
        scale = min(target / max(h, 1), target / max(w, 1))
        nh, nw = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(crop_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((target, target, 3), 114, dtype=np.uint8)
        canvas[:nh, :nw] = resized
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 128.0
        chw = np.transpose(rgb, (2, 0, 1))[None, ...]

        try:
            outs = self._det.run(None, {self._det_input: chw})
        except Exception as exc:
            print(f"[reid-face] SCRFD inference failed: {exc}", file=sys.stderr)
            return None

        # SCRFD output formats vary by export; we handle the common case of a
        # flat (N, 5) box tensor [x1, y1, x2, y2, score] returned first.
        boxes = None
        for o in outs:
            arr = np.asarray(o)
            if arr.ndim == 2 and arr.shape[-1] >= 5:
                boxes = arr
                break
        if boxes is None or boxes.shape[0] == 0:
            return None

        # Pick top-scoring box.
        idx = int(np.argmax(boxes[:, 4]))
        x1, y1, x2, y2, score = boxes[idx, :5].tolist()
        # Undo letterbox + scale.
        x1, x2 = x1 / scale, x2 / scale
        y1, y2 = y1 / scale, y2 / scale
        x1, x2 = max(0.0, x1), min(float(w), x2)
        y1, y2 = max(0.0, y1), min(float(h), y2)
        if x2 <= x1 or y2 <= y1:
            return None

        face_w = x2 - x1
        face_h = y2 - y1
        frontal_score = float(min(face_w, face_h) / max(face_w, face_h))

        return FaceDetection(
            bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
            confidence=float(score),
            frontal_score=frontal_score,
        )

    def _extract(self, crop_bgr: np.ndarray, bbox_xyxy: tuple[float, float, float, float]) -> Optional[np.ndarray]:
        x1, y1, x2, y2 = bbox_xyxy
        ix1, iy1 = max(0, int(x1)), max(0, int(y1))
        ix2 = min(crop_bgr.shape[1], int(x2))
        iy2 = min(crop_bgr.shape[0], int(y2))
        if ix2 <= ix1 or iy2 <= iy1:
            return None
        return crop_bgr[iy1:iy2, ix1:ix2]

    def _embed(self, face_bgr: np.ndarray) -> np.ndarray:
        import cv2

        face = cv2.resize(face_bgr, (112, 112), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - 127.5) / 128.0
        chw = np.transpose(rgb, (2, 0, 1))[None, ...]
        outs = self._emb.run(None, {self._emb_input: chw})
        feat = np.asarray(outs[0]).reshape(-1).astype(np.float32)
        return feat


def maybe_create_face_stack(
    *,
    enabled: bool,
    detector_onnx_path: Optional[str],
    embedder_onnx_path: Optional[str],
    dimension: int,
    min_face_size: int,
    min_frontal_score: float,
) -> Optional[FaceStack]:
    if not enabled:
        return None
    if not detector_onnx_path or not embedder_onnx_path:
        print(
            "[reid-face] face enabled but model paths missing; face fusion disabled",
            file=sys.stderr,
        )
        return None
    from pathlib import Path as _P

    if not _P(detector_onnx_path).is_file() or not _P(embedder_onnx_path).is_file():
        print(
            f"[reid-face] face enabled but onnx files missing "
            f"(detector={detector_onnx_path}, embedder={embedder_onnx_path}); "
            "face fusion disabled",
            file=sys.stderr,
        )
        return None
    try:
        return FaceStack(
            detector_onnx_path=detector_onnx_path,
            embedder_onnx_path=embedder_onnx_path,
            dimension=dimension,
            min_face_size=min_face_size,
            min_frontal_score=min_frontal_score,
        )
    except Exception as exc:
        print(f"[reid-face] failed to construct FaceStack: {exc}", file=sys.stderr)
        return None
