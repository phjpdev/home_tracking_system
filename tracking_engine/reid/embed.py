"""Body embedding backends: optional ONNX OSNet-like model, deterministic OpenCV fallback."""

from __future__ import annotations

import sys
from typing import Any, Protocol

import numpy as np

from .config import ReidConfig


class BodyEmbedder(Protocol):
    def embed_batch(self, crops_bgr: list[np.ndarray]) -> np.ndarray:
        """Return float32 array (N, dim), L2-normalized rows."""

    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...


_FALLBACK_BANNER = (
    "============================================================\n"
    "[reid] WARNING: using FallbackBodyEmbedder (grayscale heuristic).\n"
    "[reid] This is NOT production Re-ID and will confuse similar-looking people.\n"
    "[reid] Configure reid.body.onnx_model_path in YAML to point at an OSNet ONNX\n"
    "[reid] exported with tools/export_osnet_onnx.py.\n"
    "============================================================"
)


def _l2_normalize_rows(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=1, keepdims=True) + 1e-12
    return (x / n).astype(np.float32, copy=False)


class FallbackBodyEmbedder:
    """Lightweight deterministic embedding from resized grayscale (dev / no ONNX).

    Replace with ONNX OSNet when ``body.onnx_model_path`` is configured for production Re-ID quality.
    """

    def __init__(self, model_id: str, dim: int):
        self._model_id = model_id
        self._dim = dim

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    def embed_batch(self, crops_bgr: list[np.ndarray]) -> np.ndarray:
        import cv2

        feats: list[np.ndarray] = []
        for crop in crops_bgr:
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (32, 16), interpolation=cv2.INTER_AREA)
            v = g.astype(np.float64).flatten()
            v -= float(v.mean())
            n = np.linalg.norm(v) + 1e-12
            v /= n
            d = self._dim
            if v.size >= d:
                out = v[:d].astype(np.float32)
            else:
                rep = int(np.ceil(d / float(v.size)))
                out = np.tile(v.astype(np.float32), rep)[:d]
            feats.append(out)
        mat = np.stack(feats, axis=0)
        return _l2_normalize_rows(mat)


class OnnxBodyEmbedder:
    def __init__(self, onnx_path: str, model_id: str, dim: int):
        import onnxruntime as ort

        self._sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self._model_id = model_id
        self._dim = dim
        self._inp = self._sess.get_inputs()[0]
        self._name = self._inp.name

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    def embed_batch(self, crops_bgr: list[np.ndarray]) -> np.ndarray:
        import cv2

        def _spatial_dim(spec: Any, default: int) -> int:
            try:
                v = int(spec)
                return v if v > 0 else default
            except (TypeError, ValueError):
                return default

        _n, _c, ih, iw = self._inp.shape
        xh = _spatial_dim(ih, 256)
        xw = _spatial_dim(iw, 128)

        feats: list[np.ndarray] = []
        for crop in crops_bgr:
            im = cv2.resize(crop, (xw, xh), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            im_chw = np.transpose(rgb, (2, 0, 1))
            inp = im_chw[np.newaxis, ...]

            outs = self._sess.run(None, {self._name: inp})
            feat = outs[0]
            if feat.ndim == 4:
                feat = feat.reshape(feat.shape[0], -1)
            if feat.shape[-1] != self._dim:
                raise ValueError(
                    f"ONNX output dim {feat.shape[-1]} != configured body.dimension {self._dim}"
                )
            feats.append(feat.squeeze(axis=0))
        stacked = np.stack(feats, axis=0)
        return _l2_normalize_rows(stacked.astype(np.float32))


def create_body_embedder(cfg: ReidConfig) -> BodyEmbedder:
    if cfg.body_onnx_path is not None and cfg.body_onnx_path.is_file():
        try:
            return OnnxBodyEmbedder(
                str(cfg.body_onnx_path),
                cfg.body_model_id,
                cfg.body_dimension,
            )
        except Exception as exc:
            print(
                f"[reid] ONNX embedder load failed for {cfg.body_onnx_path}: {exc}",
                file=sys.stderr,
            )
            print(_FALLBACK_BANNER, file=sys.stderr)
            return FallbackBodyEmbedder(cfg.body_model_id, cfg.body_dimension)

    if cfg.body_onnx_path is not None:
        print(
            f"[reid] configured body.onnx_model_path does not exist: {cfg.body_onnx_path}",
            file=sys.stderr,
        )
    print(_FALLBACK_BANNER, file=sys.stderr)
    return FallbackBodyEmbedder(cfg.body_model_id, cfg.body_dimension)
