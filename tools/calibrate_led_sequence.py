#!/usr/bin/env python3
"""Auto-calibrate cameras using sequential LED markers on known strip geometry.

Example::

    python tools/calibrate_led_sequence.py \\
        --config tracking_engine/config.multi_camera.yaml \\
        --cameras cam_yoga_ne,cam_yoga_se,cam_hallway_n

    python tools/calibrate_led_sequence.py --config ... --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracking_engine.calibrate_web.calibration_io import atomic_merge_calibrations
from tracking_engine.calibration.auto_led import LedAutoCalConfig, LedAutoCalibrator
from tracking_engine.pipeline.camera_undistort import CameraUndistortRegistry
from tracking_engine.pipeline.ingest import open_rtsp_latest
from tracking_engine.pipeline.maro_floorplan import COORDINATE_SPACE


def _resolve(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (cfg_dir / path).resolve()


def _norm_rotate(raw: Any) -> int:
    try:
        deg = int(raw or 0) % 360
    except (TypeError, ValueError):
        return 0
    return deg if deg in (0, 90, 180, 270) else 0


def _apply_rotate(frame: np.ndarray, deg: int) -> np.ndarray:
    if deg == 0 or frame is None:
        return frame
    if deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def _load_camera_streams(cfg_path: Path) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg_dir = cfg_path.parent
    mc = cfg.get("multi_camera") or {}
    streams: list[dict[str, Any]] = []
    for row in mc.get("streams") or []:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        name = str(row.get("name", "")).strip()
        url = str(row.get("rtsp_url", "")).strip()
        if name and url:
            streams.append(
                {
                    "name": name,
                    "url": url,
                    "rotate": _norm_rotate(row.get("rotate")),
                }
            )
    return cfg, cfg_dir, streams


def _grab_factory(
    streams: list[dict[str, Any]],
    undistort: CameraUndistortRegistry,
    *,
    settle_frames: int = 3,
) -> tuple[Any, Callable[[str], Optional[np.ndarray]], Callable[[], None]]:
    sources: dict[str, Any] = {}
    for s in streams:
        cap = open_rtsp_latest(s["url"])
        sources[s["name"]] = (cap, s["rotate"])

    def grab(cam_id: str) -> Optional[np.ndarray]:
        entry = sources.get(cam_id)
        if not entry:
            return None
        cap, rotate = entry
        frame = None
        for _ in range(max(1, settle_frames)):
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
        if frame is not None and rotate:
            frame = _apply_rotate(frame, rotate)
        if frame is not None and undistort.has(cam_id):
            frame = undistort.apply(cam_id, frame)
        return frame

    def release() -> None:
        for cap, _ in sources.values():
            try:
                cap.release()
            except Exception:
                pass

    return sources, grab, release


def main() -> int:
    ap = argparse.ArgumentParser(description="LED sequence auto-calibration")
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("tracking_engine/config.multi_camera.yaml"),
    )
    ap.add_argument(
        "--cameras",
        type=str,
        default="",
        help="Comma-separated camera names (default: all enabled streams)",
    )
    ap.add_argument("--strip", type=str, default="", help="Single strip name (default: all)")
    ap.add_argument("--step", type=int, default=0, help="Marker step (0 = use config)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refine-tiles", action="store_true", help="Run tile grid refine after LED cal")
    ap.add_argument("--tile-mm", type=float, default=600.0, help="Tile width for --refine-tiles")
    args = ap.parse_args()

    cfg_path = args.config.resolve()
    if not cfg_path.is_file():
        print(f"config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg, cfg_dir, streams = _load_camera_streams(cfg_path)
    if not streams:
        print("no enabled streams in config", file=sys.stderr)
        return 2

    cam_names = [s["name"] for s in streams]
    if args.cameras.strip():
        want = {x.strip() for x in args.cameras.split(",") if x.strip()}
        cam_names = [c for c in cam_names if c in want]
    if not cam_names:
        print("no cameras selected", file=sys.stderr)
        return 2

    auto_cfg_raw = cfg.get("auto_calibration") or {}
    led_raw = auto_cfg_raw.get("led") or {}
    acfg = LedAutoCalConfig(
        marker_step=int(args.step or led_raw.get("marker_step", 5)),
        settle_ms=int(led_raw.get("settle_ms", 400)),
        min_markers=int(led_raw.get("min_markers", 6)),
        save_residual_px_max=float(led_raw.get("save_residual_px_max", 8.0)),
        strip_names=[args.strip] if args.strip else None,
        dry_run=bool(args.dry_run),
    )

    maro_cfg = cfg.get("maro") or {}
    cache_dir = _resolve(cfg_dir, str(maro_cfg.get("assets_cache_dir", "calibration/maro_cache")))
    api_base = str(maro_cfg.get("api_base") or "http://127.0.0.1:8420")
    calib_path = _resolve(cfg_dir, str(cfg.get("calibration_file", "calibration/camera_calibrations.json")))
    intrinsics_path = _resolve(
        cfg_dir, str(cfg.get("intrinsics_file", "calibration/camera_intrinsics.json"))
    )
    undistort = CameraUndistortRegistry.from_path(intrinsics_path)

    strips_path = auto_cfg_raw.get("strips_path")
    mapping_path = auto_cfg_raw.get("artnet_mapping_path")
    sp = Path(strips_path) if strips_path else None
    mp = Path(mapping_path) if mapping_path else None

    _, grab, release_caps = _grab_factory(
        [s for s in streams if s["name"] in cam_names],
        undistort,
    )
    new_entries: dict[str, dict[str, Any]] = {}
    any_ok = False
    try:
        calibrator = LedAutoCalibrator.from_paths(
            cache_dir=cache_dir,
            maro_api_base=api_base,
            cfg=acfg,
            grab_frame=grab,
            strips_path=sp,
            mapping_path=mp,
        )

        for cam_id in cam_names:
            print(f"[led-cal] {cam_id} ...", flush=True)
            result = calibrator.calibrate_camera(
                cam_id,
                strip_name=args.strip or None,
            )
            if not result.ok:
                print(f"  FAIL: {result.error}", file=sys.stderr)
                continue
            frame = grab(cam_id)
            if frame is None:
                print("  FAIL: could not grab frame for dimensions", file=sys.stderr)
                continue
            h, w = frame.shape[:2]
            entry = calibrator.build_calibration_entry(result, img_w=w, img_h=h)
            new_entries[cam_id] = entry
            any_ok = True
            print(
                f"  OK: n={result.num_markers} mean={result.residual_px_mean:.2f}px "
                f"max={result.residual_px_max:.2f}px"
            )

            if args.refine_tiles and not args.dry_run:
                from tools.calibrate_tile_grid import refine_homography_from_tiles

                refined = refine_homography_from_tiles(
                    frame,
                    entry,
                    tile_mm=args.tile_mm,
                    undistort=undistort if undistort.has(cam_id) else None,
                    cam_id=cam_id,
                )
                if refined is not None:
                    entry = refined
                    new_entries[cam_id] = entry
                    print("  tile refine: applied", flush=True)
                else:
                    print("  tile refine: skipped (low confidence)", flush=True)
    finally:
        release_caps()

    if args.dry_run:
        print("[led-cal] dry-run complete (no files written)")
        return 0

    if not any_ok:
        print("[led-cal] no camera calibrated successfully", file=sys.stderr)
        return 1

    plan_size = [
        int(new_entries[cam_names[0]].get("plan_bounds_px", {}).get("x_max", 2700)),
        int(new_entries[cam_names[0]].get("plan_bounds_px", {}).get("y_max", 1324)),
    ]
    atomic_merge_calibrations(
        calib_path,
        new_entries,
        document_meta={
            "_coordinate_space": COORDINATE_SPACE,
            "_plan_size_px": plan_size,
        },
    )
    print(f"[led-cal] wrote {len(new_entries)} camera(s) to {calib_path}")
    print("[led-cal] restart tracking-engine to load new calibration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
