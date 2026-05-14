"""YAML-driven Re-ID configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ReidConfig:
    enabled: bool
    backend: str
    sqlite_path: Path
    faiss_path: Path
    tenant_id: str
    site_id: Optional[str]
    body_model_id: str
    body_dimension: int
    body_onnx_path: Optional[Path]
    threshold_match: float
    threshold_high: float
    ema_alpha: float
    top_k: int
    min_bbox_area: float
    min_blur_var: float
    min_aspect: float
    max_aspect: float
    face_enabled: bool
    max_prototypes_per_identity: int
    min_quality_to_store: float
    dedup_distance: float
    max_writes_per_minute: int
    tentative_to_confirmed_frames: int
    mapping_ttl_sec: float
    ema_reset_gap_sec: float

    @staticmethod
    def from_cfg(cfg: dict[str, Any], cfg_dir: Path) -> "ReidConfig":
        r = cfg.get("reid") or {}
        if not isinstance(r, dict):
            r = {}
        enabled = bool(r.get("enabled", False))
        backend = str(r.get("backend", "sqlite_faiss")).lower()

        dsn = str(r.get("dsn", "sqlite:///gallery.db"))
        sqlite_path = _resolve_sqlite_dsn(dsn, cfg_dir)

        faiss_rel = str(r.get("faiss_path", "gallery_body.faiss"))
        faiss_path = Path(faiss_rel)
        if not faiss_path.is_absolute():
            faiss_path = (cfg_dir / faiss_path).resolve()

        tenant_id = str(r.get("tenant_id", "default"))
        site_id = r.get("site_id")
        site_id = str(site_id) if site_id is not None else None

        body = r.get("body") or {}
        if not isinstance(body, dict):
            body = {}
        body_model_id = str(body.get("model_id", "osnet_x025_v1"))
        body_dimension = int(body.get("dimension", 512))
        onnx_raw = body.get("onnx_model_path")
        body_onnx_path: Optional[Path] = None
        if onnx_raw:
            p = Path(str(onnx_raw))
            body_onnx_path = p if p.is_absolute() else (cfg_dir / p).resolve()

        threshold_match = float(body.get("threshold_match", 0.35))
        threshold_high = float(body.get("threshold_high", 0.20))
        ema_alpha = float(body.get("ema_alpha", 0.3))
        top_k = int(body.get("top_k", 5))
        min_bbox_area = float(body.get("min_bbox_area", 4000))
        min_blur_var = float(body.get("min_blur_var", 50))
        min_aspect = float(body.get("min_aspect", 1.5))
        max_aspect = float(body.get("max_aspect", 4.0))

        face = r.get("face") or {}
        face_enabled = bool(face.get("enabled", False)) if isinstance(face, dict) else False

        gallery = r.get("gallery") or {}
        if not isinstance(gallery, dict):
            gallery = {}
        max_prototypes_per_identity = int(gallery.get("max_prototypes_per_identity", 30))
        min_quality_to_store = float(gallery.get("min_quality_to_store", 0.7))
        dedup_distance = float(gallery.get("dedup_distance", 0.05))
        max_writes_per_minute = int(gallery.get("max_writes_per_minute", 60))

        fusion = r.get("fusion") or {}
        if not isinstance(fusion, dict):
            fusion = {}
        tentative_to_confirmed_frames = int(fusion.get("tentative_to_confirmed_frames", 15))
        mapping_ttl_sec = float(fusion.get("mapping_ttl_sec", 5.0))
        ema_reset_gap_sec = float(fusion.get("ema_reset_gap_sec", 2.0))

        return ReidConfig(
            enabled=enabled,
            backend=backend,
            sqlite_path=sqlite_path,
            faiss_path=faiss_path,
            tenant_id=tenant_id,
            site_id=site_id,
            body_model_id=body_model_id,
            body_dimension=body_dimension,
            body_onnx_path=body_onnx_path,
            threshold_match=threshold_match,
            threshold_high=threshold_high,
            ema_alpha=ema_alpha,
            top_k=top_k,
            min_bbox_area=min_bbox_area,
            min_blur_var=min_blur_var,
            min_aspect=min_aspect,
            max_aspect=max_aspect,
            face_enabled=face_enabled,
            max_prototypes_per_identity=max_prototypes_per_identity,
            min_quality_to_store=min_quality_to_store,
            dedup_distance=dedup_distance,
            max_writes_per_minute=max_writes_per_minute,
            tentative_to_confirmed_frames=tentative_to_confirmed_frames,
            mapping_ttl_sec=mapping_ttl_sec,
            ema_reset_gap_sec=ema_reset_gap_sec,
        )


def _resolve_sqlite_dsn(dsn: str, cfg_dir: Path) -> Path:
    s = dsn.strip()
    if s.startswith("sqlite:///"):
        path = Path(s[len("sqlite:///") :])
    elif s.startswith("sqlite://"):
        path = Path(s[len("sqlite://") :])
    else:
        path = Path(s)
    if not path.is_absolute():
        path = (cfg_dir / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
