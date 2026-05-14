#!/usr/bin/env python3
"""Face enrollment CLI.

Opens an RTSP feed (or local clip via ``--video``), runs SCRFD to collect
high-quality frontal head crops, embeds them with ArcFace, and writes the
results plus a ``consent_record`` row to the SQLite gallery.

Usage:

    python -m tracking_engine.enroll \\
        --config tracking_engine/config.multi_camera.yaml \\
        --camera cam_kwz_sw \\
        --name "Jean Patrick" \\
        --frames 5 \\
        --consent-basis consent
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from .pipeline.cameras_layout import load_cameras_layout, resolve_active_streams
from .pipeline.detector import create_detector
from .pipeline.ingest import open_rtsp, open_rtsp_latest, open_video
from .reid.config import ReidConfig
from .reid.crop_quality import clipped_head_bbox, person_crop
from .reid.face_embed import FaceStack, maybe_create_face_stack
from .reid.gallery_sqlite import GallerySqliteFaiss


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_path(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (cfg_dir / path).resolve()


def _open_source(cam: dict[str, Any], video_override: str | None, use_latest_rtsp: bool):
    if video_override:
        vp = Path(video_override).expanduser()
        if not vp.is_file():
            raise FileNotFoundError(f"video file not found: {vp}")
        return open_video(str(vp))
    rtsp_url = cam["rtsp_url"]
    return open_rtsp_latest(rtsp_url) if use_latest_rtsp else open_rtsp(rtsp_url)


def run(args: argparse.Namespace) -> int:
    cfg_path = args.config.resolve()
    if not cfg_path.is_file():
        print(f"[enroll] config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = _load_yaml(cfg_path)
    cfg_dir = cfg_path.parent

    layout_rel = cfg.get("multi_camera", {}).get("cameras_layout_file")
    if not layout_rel:
        print("[enroll] multi_camera.cameras_layout_file missing in config", file=sys.stderr)
        return 2
    layout = load_cameras_layout(_resolve_path(cfg_dir, str(layout_rel)))

    streams_yaml = cfg.get("multi_camera", {}).get("streams") or []
    cameras = resolve_active_streams(layout, streams_yaml, {})
    cam = next((c for c in cameras if c["name"] == args.camera), None)
    if cam is None:
        names = [c["name"] for c in cameras]
        print(
            f"[enroll] camera {args.camera!r} not enabled. Active: {names}",
            file=sys.stderr,
        )
        return 2

    reid_cfg = ReidConfig.from_cfg(cfg, cfg_dir)
    if not reid_cfg.face_enabled:
        print(
            "[enroll] reid.face.enabled is false in config; enable it first.",
            file=sys.stderr,
        )
        return 2

    face = maybe_create_face_stack(
        enabled=True,
        detector_onnx_path=str(reid_cfg.face_detector_onnx_path) if reid_cfg.face_detector_onnx_path else None,
        embedder_onnx_path=str(reid_cfg.face_embedder_onnx_path) if reid_cfg.face_embedder_onnx_path else None,
        dimension=reid_cfg.face_dimension,
        min_face_size=reid_cfg.face_min_face_size,
        min_frontal_score=reid_cfg.face_min_frontal_score,
    )
    if face is None:
        print("[enroll] face stack unavailable; refusing to enroll", file=sys.stderr)
        return 2

    detector, det_cleanup = create_detector(cfg)
    gallery = GallerySqliteFaiss(reid_cfg)
    source = _open_source(
        cam,
        args.video,
        bool(cfg.get("multi_camera", {}).get("use_latest_frame_rtsp", True)),
    )

    print(f"[enroll] capturing {args.frames} good face crops from {cam['name']}...", file=sys.stderr)

    collected: list[dict[str, Any]] = []
    started = time.time()
    timeout = float(args.timeout_sec)
    try:
        while len(collected) < int(args.frames):
            if (time.time() - started) > timeout:
                print(f"[enroll] timeout after {timeout:.0f}s; collected {len(collected)} frames", file=sys.stderr)
                break
            ok, frame = source.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue

            detections = detector.detect(frame, latency_record=None)
            if detections is None or len(detections) == 0:
                continue

            # Pick the largest person bbox.
            best_i = 0
            best_area = 0.0
            for i in range(len(detections)):
                x1, y1, x2, y2 = [float(v) for v in detections.xyxy[i]]
                area = max((x2 - x1) * (y2 - y1), 0.0)
                if area > best_area:
                    best_area = area
                    best_i = i
            x1, y1, x2, y2 = [float(v) for v in detections.xyxy[best_i]]
            crop = person_crop(frame, (x1, y1, x2, y2))
            if crop is None:
                continue

            fh, fw = frame.shape[:2]
            hx1, hy1, hx2, hy2 = clipped_head_bbox((x1, y1, x2, y2), fh, fw)
            if hx2 <= hx1 or hy2 <= hy1:
                continue
            head_crop = frame[hy1:hy2, hx1:hx2]

            face_res = face.detect_and_embed_head(head_crop)
            if face_res is None:
                continue

            collected.append(
                {
                    "embedding": face_res.embedding,
                    "quality": float(face_res.detection.confidence),
                    "frontal": float(face_res.detection.frontal_score),
                    "ts": time.time(),
                }
            )
            print(
                f"[enroll]   captured {len(collected)}/{args.frames} "
                f"(conf={face_res.detection.confidence:.2f}, "
                f"frontal={face_res.detection.frontal_score:.2f})",
                file=sys.stderr,
            )
    finally:
        source.release()
        if det_cleanup:
            det_cleanup()

    if not collected:
        print("[enroll] no qualifying face crops collected — aborting", file=sys.stderr)
        gallery.close()
        return 1

    identity_id = gallery.create_identity(
        display_name=args.name,
        tenant_id=reid_cfg.tenant_id,
        site_id=reid_cfg.site_id,
        enrolled=True,
    )
    for c in collected:
        gallery.insert_face(
            identity_id=identity_id,
            embedding=c["embedding"],
            quality=c["quality"],
            cam_id=cam["name"],
            frame_ts=c["ts"],
            enrolled=True,
        )
    gallery.upsert_consent(
        identity_id=identity_id,
        lawful_basis=args.consent_basis,
        retention_days=args.retention_days,
    )

    print("[enroll] success", file=sys.stderr)
    print(f"identity_id={identity_id}")
    print(f"display_name={args.name}")
    print(f"faces_captured={len(collected)}")
    print(f"consent_basis={args.consent_basis}")
    gallery.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--camera", required=True, help="layout name of the source camera")
    ap.add_argument("--name", required=True, help="display name of the new identity")
    ap.add_argument("--frames", type=int, default=5, help="number of face shots to collect")
    ap.add_argument(
        "--consent-basis",
        default="consent",
        choices=["consent", "legitimate_interest", "contract"],
        help="GDPR lawful basis to record",
    )
    ap.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="optional retention horizon (days); default = indefinite until revoked",
    )
    ap.add_argument(
        "--video",
        type=str,
        default=None,
        help="local video file override (skips RTSP)",
    )
    ap.add_argument(
        "--timeout-sec",
        type=float,
        default=60.0,
        help="maximum seconds to wait for the requested number of shots",
    )
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
