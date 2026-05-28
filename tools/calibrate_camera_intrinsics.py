#!/usr/bin/env python3
"""Calibrate camera intrinsics (K, dist) from chessboard images or a video/RTSP stream.

Writes/merges into ``tracking_engine/calibration/camera_intrinsics.json``.
Used for wide-angle hallway camera undistortion before homography calibration.

Examples
--------
From RTSP (uses rotate from config for that camera)::

    python tools/calibrate_camera_intrinsics.py \\
        --camera cam_hallway_n \\
        --config tracking_engine/config.multi_camera.yaml \\
        --frames 40

From a folder of PNG captures::

    python tools/calibrate_camera_intrinsics.py \\
        --camera cam_hallway_n \\
        --images stills/hallway_calib/*.png
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracking_engine.pipeline.cameras_layout import load_cameras_layout, resolve_active_streams
from tracking_engine.pipeline.ingest import open_rtsp_latest, open_video


def _resolve(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (cfg_dir / path).resolve()


def _apply_rotate(frame: np.ndarray, deg: int) -> np.ndarray:
    if deg == 0:
        return frame
    if deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def _find_corners(gray: np.ndarray, pattern_size: tuple[int, int]) -> tuple[bool, np.ndarray | None]:
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    ok, corners = cv2.findChessboardCorners(gray, pattern_size, flags)
    if not ok:
        return False, None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
    return True, corners


def _collect_from_stream(
    cap,
    *,
    rotate: int,
    pattern_size: tuple[int, int],
    max_frames: int,
    min_good: int,
    settle_sec: float = 0.0,
    save_debug: Path | None = None,
) -> tuple[list[np.ndarray], list[np.ndarray], tuple[int, int]]:
    objp = np.zeros((pattern_size[0] * pattern_size[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0 : pattern_size[0], 0 : pattern_size[1]].T.reshape(-1, 2)
    objpoints: list[np.ndarray] = []
    imgpoints: list[np.ndarray] = []
    image_size: tuple[int, int] | None = None
    frames_read = 0
    last_frame: np.ndarray | None = None

    if settle_sec > 0:
        import time

        print(f"  waiting {settle_sec:.1f}s for RTSP to stabilize...", flush=True)
        time.sleep(settle_sec)

    attempts = max(max_frames * 5, 60)
    for _ in range(attempts):
        if len(objpoints) >= max_frames:
            break
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        frames_read += 1
        frame = _apply_rotate(frame, rotate)
        last_frame = frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found, corners = _find_corners(gray, pattern_size)
        if not found or corners is None:
            continue
        h, w = gray.shape[:2]
        image_size = (w, h)
        objpoints.append(objp.copy())
        imgpoints.append(corners)
        print(f"  corner frame {len(objpoints)}/{max_frames}", flush=True)

    if image_size is None or len(objpoints) < min_good:
        if save_debug and last_frame is not None:
            save_debug.mkdir(parents=True, exist_ok=True)
            out = save_debug / "last_frame_no_chessboard.jpg"
            cv2.imwrite(str(out), last_frame)
            print(f"  saved debug frame to {out}", flush=True)
        raise RuntimeError(
            f"need >= {min_good} chessboard detections, got {len(objpoints)} "
            f"(pattern inner corners {pattern_size[0]}x{pattern_size[1]}, "
            f"frames_read={frames_read}).\n"
            "This tool does NOT use Maro — only the camera RTSP + a physical chessboard.\n"
            "Check: (1) print a checkerboard and fill most of the hallway view, "
            "(2) --cols/--rows match INNER corners (9x6 default), "
            "(3) probe_rtsp saves a still first, "
            "(4) use /opt/tracking-system/.venv/bin/python."
        )
    return objpoints, imgpoints, image_size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera", required=True, help="camera id, e.g. cam_hallway_n")
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--images", type=Path, help="directory of still images (instead of RTSP)")
    ap.add_argument("--rtsp", help="override RTSP URL")
    ap.add_argument("--out", type=Path, help="intrinsics JSON (default from config intrinsics_file)")
    ap.add_argument("--cols", type=int, default=9, help="inner chessboard corners (columns)")
    ap.add_argument("--rows", type=int, default=6, help="inner chessboard corners (rows)")
    ap.add_argument("--square-mm", type=float, default=25.0, help="square size for object points (mm)")
    ap.add_argument("--frames", type=int, default=30, help="max good frames to collect from stream")
    ap.add_argument("--min-frames", type=int, default=12, help="minimum good frames required")
    ap.add_argument("--preview", action="store_true", help="show last undistorted preview window")
    ap.add_argument(
        "--settle-sec",
        type=float,
        default=3.0,
        help="seconds to wait after opening RTSP before sampling (default 3)",
    )
    ap.add_argument(
        "--save-debug",
        type=Path,
        default=None,
        help="if detection fails, write last frame here (e.g. /tmp/hallway_debug)",
    )
    args = ap.parse_args()

    pattern_size = (args.cols, args.rows)
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    cfg_dir = args.config.parent.resolve()
    out_path = _resolve(cfg_dir, str(args.out or cfg.get("intrinsics_file", "calibration/camera_intrinsics.json")))

    rotate = 0
    if args.images:
        paths = sorted(args.images.glob("*"))
        paths = [p for p in paths if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}]
        if not paths:
            print(f"no images in {args.images}", file=sys.stderr)
            return 2

        class _Cap:
            def __init__(self, files: list[Path]):
                self._files = files
                self._i = 0

            def read(self):
                if self._i >= len(self._files):
                    return False, None
                img = cv2.imread(str(self._files[self._i]))
                self._i += 1
                return img is not None, img

        cap = _Cap(paths)
    else:
        mc = cfg.get("multi_camera") or {}
        layout = load_cameras_layout(_resolve(cfg_dir, str(mc.get("cameras_layout_file"))))
        streams = resolve_active_streams(layout, mc.get("streams") or [], {})
        cam_row = next((c for c in streams if c["name"] == args.camera), None)
        if cam_row is None:
            print(f"camera {args.camera!r} not in active streams", file=sys.stderr)
            return 2
        rotate = int(cam_row.get("rotate") or 0)
        url = args.rtsp or str(cam_row.get("rtsp_url") or "")
        if not url:
            print("no rtsp_url", file=sys.stderr)
            return 2
        print(f"  RTSP {args.camera} rotate={rotate} url={url[:60]}...", flush=True)
        cap = open_rtsp_latest(url)

    try:
        objpoints, imgpoints, (w, h) = _collect_from_stream(
            cap,
            rotate=rotate,
            pattern_size=pattern_size,
            max_frames=args.frames,
            min_good=args.min_frames,
            settle_sec=0.0 if args.images else float(args.settle_sec),
            save_debug=args.save_debug,
        )
    finally:
        if hasattr(cap, "release"):
            cap.release()

    for op in objpoints:
        op *= float(args.square_mm)

    rms, K, dist, _rvecs, _tvecs = cv2.calibrateCamera(
        objpoints, imgpoints, (w, h), None, None
    )
    print(f"calibrateCamera RMS reprojection error: {rms:.3f} px")

    entry = {
        "K": K.tolist(),
        "dist": dist.flatten().tolist(),
        "image_size": [w, h],
        "alpha": 0.0,
        "pattern": {"cols": args.cols, "rows": args.rows, "square_mm": args.square_mm},
        "rms_reprojection_px": float(rms),
    }

    existing: dict = {}
    if out_path.is_file():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
    for key in list(existing.keys()):
        if not str(key).startswith("_") and key != args.camera:
            pass
    existing[args.camera] = entry
    if "_comment" not in existing:
        existing["_comment"] = "Per-camera OpenCV intrinsics for cv2.undistort in multi_camera / calibrate_web."

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"wrote {args.camera} intrinsics to {out_path}")

    if args.preview and args.images:
        first = next(
            (p for p in sorted(args.images.glob("*")) if p.suffix.lower() in {".png", ".jpg", ".jpeg"}),
            None,
        )
        if first is not None:
            sample = cv2.imread(str(first))
            if sample is not None:
                sample = _apply_rotate(sample, rotate)
                map1, map2 = cv2.initUndistortRectifyMap(
                    K, dist, None, K, (w, h), cv2.CV_16SC2
                )
                und = cv2.remap(sample, map1, map2, cv2.INTER_LINEAR)
                cv2.imshow("undistort preview (q to close)", np.hstack([sample, und]))
                cv2.waitKey(0)
                cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
