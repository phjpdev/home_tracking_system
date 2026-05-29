#!/usr/bin/env python3
"""Line-based (plumb-line) lens distortion calibration from a single still.

For cameras where a printed chessboard is impractical, calibrate radial
distortion using features that are physically straight -- floor tile grout
lines, an LED strip, a wall base -- and solve for the distortion coefficients
that make them straight again. Writes/merges the same
``tracking_engine/calibration/camera_intrinsics.json`` entry the chessboard
tool (``calibrate_camera_intrinsics.py``) produces, so the rest of the
pipeline is unchanged.

Run this on a machine with a display (e.g. a Mac) using a still that was saved
WITH the camera's ``rotate`` already applied (e.g. the PNG from
``tools/probe_rtsp.py --save-stills``), because the runtime undistorts AFTER
rotating.

Workflow
--------
1. A window opens showing the still.
2. Left-click several points (>=3) along a line you KNOW is straight in reality.
   Press ``n`` to finish that line and start the next. Add several lines,
   ideally in two directions (tiles along + across the corridor) plus the LED
   strip and a wall edge.
3. Press ``c`` (or Enter) to solve. A before/after preview opens.
4. Press ``s`` in the preview to save the intrinsics, or any other key to keep
   editing.

Keys: left-click add point | n next line | u undo point | r reset all |
      c/Enter compute | s save (in preview) | q/Esc quit without saving

Example
-------
    python tools/calibrate_distortion_lines.py \\
        --image stills/cam_hallway_n.png --camera cam_hallway_n
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

try:
    from scipy.optimize import least_squares
except ImportError:  # pragma: no cover - dependency hint
    print(
        "This tool needs SciPy: pip install scipy  (or add it to requirements).",
        file=sys.stderr,
    )
    raise

ROOT = Path(__file__).resolve().parents[1]

WINDOW = "distortion calib (click lines)"
PREVIEW = "before / after (s=save)"
_LINE_COLORS = [
    (0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
    (255, 0, 255), (255, 255, 0), (0, 128, 255), (128, 255, 0),
]


def _resolve(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (cfg_dir / path).resolve()


def _build_K(f: float, cx: float, cy: float) -> np.ndarray:
    return np.array([[f, 0.0, cx], [0.0, f, cy], [0.0, 0.0, 1.0]], dtype=np.float64)


def _build_dist(k1: float, k2: float, k3: float) -> np.ndarray:
    return np.array([k1, k2, 0.0, 0.0, k3], dtype=np.float64)


def _undistort_line(pts: np.ndarray, K: np.ndarray, dist: np.ndarray) -> np.ndarray:
    src = pts.reshape(-1, 1, 2).astype(np.float32)
    und = cv2.undistortPoints(src, K, dist, P=K)
    return und.reshape(-1, 2)


def _line_residuals(pts: np.ndarray) -> np.ndarray:
    """Perpendicular distances of points to their total-least-squares best line."""
    mean = pts.mean(axis=0)
    centered = pts - mean
    _u, _s, vt = np.linalg.svd(centered, full_matrices=False)
    normal = vt[1]  # direction of least variance
    return centered @ normal


def calibrate(
    lines: list[np.ndarray],
    image_size: tuple[int, int],
    fit_k3: bool,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    w, h = image_size
    cx, cy = w / 2.0, h / 2.0

    def residuals(params: np.ndarray) -> np.ndarray:
        f = params[0]
        k1 = params[1]
        k2 = params[2]
        k3 = params[3] if fit_k3 else 0.0
        K = _build_K(f, cx, cy)
        dist = _build_dist(k1, k2, k3)
        out: list[np.ndarray] = []
        for ln in lines:
            und = _undistort_line(ln, K, dist)
            out.append(_line_residuals(und))
        return np.concatenate(out)

    f0 = float(w) * 0.9
    x0 = [f0, 0.0, 0.0] + ([0.0] if fit_k3 else [])
    lo = [0.3 * w, -2.0, -2.0] + ([-2.0] if fit_k3 else [])
    hi = [4.0 * w, 2.0, 2.0] + ([2.0] if fit_k3 else [])

    rms_before = float(np.sqrt(np.mean(residuals(np.array(x0)) ** 2)))
    sol = least_squares(residuals, x0, bounds=(lo, hi), method="trf", max_nfev=2000)
    rms_after = float(np.sqrt(np.mean(sol.fun ** 2)))

    f = sol.x[0]
    k1, k2 = sol.x[1], sol.x[2]
    k3 = sol.x[3] if fit_k3 else 0.0
    return _build_K(f, cx, cy), _build_dist(k1, k2, k3), rms_before, rms_after


def make_preview(img: np.ndarray, K: np.ndarray, dist: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    new_k, _roi = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), alpha=0.0, newImgSize=(w, h))
    map1, map2 = cv2.initUndistortRectifyMap(K, dist, None, new_k, (w, h), cv2.CV_16SC2)
    und = cv2.remap(img, map1, map2, interpolation=cv2.INTER_LINEAR)
    label_a = img.copy()
    cv2.putText(label_a, "BEFORE", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    cv2.putText(und, "AFTER", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    return np.hstack([label_a, und])


def run_gui(img: np.ndarray, scale: float, fit_k3: bool):
    disp_base = cv2.resize(img, None, fx=scale, fy=scale) if scale != 1.0 else img.copy()
    lines: list[list[tuple[float, float]]] = [[]]

    def redraw():
        canvas = disp_base.copy()
        for li, ln in enumerate(lines):
            color = _LINE_COLORS[li % len(_LINE_COLORS)]
            for j, (x, y) in enumerate(ln):
                p = (int(x * scale), int(y * scale))
                cv2.circle(canvas, p, 4, color, -1)
                if j > 0:
                    q = (int(ln[j - 1][0] * scale), int(ln[j - 1][1] * scale))
                    cv2.line(canvas, q, p, color, 1)
        n_done = sum(1 for ln in lines if len(ln) >= 3)
        msg = f"lines:{n_done} ok / current pts:{len(lines[-1])}  [n]next [u]undo [r]reset [c]compute [q]quit"
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 22), (0, 0, 0), -1)
        cv2.putText(canvas, msg, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        cv2.imshow(WINDOW, canvas)

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            lines[-1].append((x / scale, y / scale))
            redraw()

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    redraw()

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("q"), 27):
            cv2.destroyAllWindows()
            return None
        if key == ord("u"):
            if lines[-1]:
                lines[-1].pop()
            elif len(lines) > 1:
                lines.pop()
            redraw()
        elif key == ord("r"):
            lines = [[]]
            redraw()
        elif key == ord("n"):
            if len(lines[-1]) >= 3:
                lines.append([])
            redraw()
        elif key in (ord("c"), 13):
            usable = [np.array(ln, dtype=np.float64) for ln in lines if len(ln) >= 3]
            if len(usable) < 2:
                print("[calib] need at least 2 lines with >=3 points each", file=sys.stderr)
                continue
            h, w = img.shape[:2]
            K, dist, rms0, rms1 = calibrate(usable, (w, h), fit_k3)
            print(f"[calib] lines={len(usable)}  line-straightness RMS: {rms0:.2f}px -> {rms1:.2f}px")
            print(f"[calib] f={K[0,0]:.1f}  dist={dist.tolist()}")
            preview = make_preview(img, K, dist)
            cv2.imshow(PREVIEW, preview)
            pk = cv2.waitKey(0) & 0xFF
            cv2.destroyWindow(PREVIEW)
            if pk == ord("s"):
                cv2.destroyAllWindows()
                return K, dist, (w, h), rms1
            redraw()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, required=True, help="still saved WITH rotate applied (e.g. probe_rtsp PNG)")
    ap.add_argument("--camera", required=True, help="camera id, e.g. cam_hallway_n")
    ap.add_argument("--config", type=Path, default=ROOT / "tracking_engine/config.multi_camera.yaml")
    ap.add_argument("--out", type=Path, help="intrinsics JSON (default from config intrinsics_file)")
    ap.add_argument("--scale", type=float, default=0.0, help="display scale (0 = auto-fit to ~900px tall)")
    ap.add_argument("--no-k3", action="store_true", help="fit only k1,k2 (default also fits k3)")
    args = ap.parse_args()

    if not args.image.is_file():
        print(f"image not found: {args.image}", file=sys.stderr)
        return 2
    img = cv2.imread(str(args.image))
    if img is None:
        print(f"could not read image: {args.image}", file=sys.stderr)
        return 2

    h = img.shape[0]
    scale = args.scale if args.scale > 0 else (min(1.0, 900.0 / h) if h > 900 else 1.0)

    import yaml

    out_path = args.out
    if out_path is None:
        cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        cfg_dir = args.config.parent.resolve()
        out_path = _resolve(cfg_dir, str(cfg.get("intrinsics_file", "calibration/camera_intrinsics.json")))

    print(f"[calib] image {args.image} ({img.shape[1]}x{img.shape[0]})  display scale={scale:.3f}")
    print("[calib] click >=3 points per straight line; 'n' for next line; 'c' to compute.")

    result = run_gui(img, scale, fit_k3=not args.no_k3)
    if result is None:
        print("[calib] cancelled, nothing written")
        return 1

    K, dist, (w, hh), rms = result
    entry = {
        "K": K.tolist(),
        "dist": dist.flatten().tolist(),
        "image_size": [w, hh],
        "alpha": 0.0,
        "method": "plumb_line",
        "line_straightness_rms_px": float(rms),
    }

    existing: dict = {}
    if out_path.is_file():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
    existing[args.camera] = entry
    if "_comment" not in existing:
        existing["_comment"] = "Per-camera OpenCV intrinsics for cv2.undistort in multi_camera / calibrate_web."

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"[calib] wrote {args.camera} intrinsics to {out_path}")
    print("[calib] restart tracking-calibrate-web and tracking-engine to reload undistort maps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
