#!/usr/bin/env python3
"""Interactive per-camera homography calibration.

Builds the ``mode: "homography"`` entries in
``tracking_engine/calibration/camera_calibrations.json`` consumed at runtime
by :func:`tracking_engine.pipeline.homography.foot_point_to_mm`.

Workflow per camera
-------------------
1. Capture a still frame from the live RTSP feed (or use a saved image).
2. Click >= 4 well-spread floor-plane points (corners of a rug, doorway
   thresholds, room corners — anything you can also locate on the floor
   plan in millimetres).
3. For each clicked image point, type the corresponding ``x_mm y_mm``
   from the floor plan (origin = NW corner of the envelope, +x east,
   +y south, units millimetres). The script stores the projection.
4. The script computes ``H`` with ``cv2.findHomography(...)`` and
   merges it into the JSON. Existing entries for other cameras are
   preserved.

Usage
-----
.. code-block:: bash

   # Live RTSP grab on the Pi:
   python tools/calibrate_homography.py \
       --camera cam_kwz_sw \
       --rtsp rtsp://192.168.1.41:554/live \
       --out tracking_engine/calibration/camera_calibrations.json

   # Offline (use a saved still):
   python tools/calibrate_homography.py \
       --camera cam_kwz_sw \
       --image stills/cam_kwz_sw.png \
       --out tracking_engine/calibration/camera_calibrations.json

Tip: open ``camera_placement_plan/floor_plan.png`` next to the calibration
window. Pick floor points that are easy to identify in both views — door
sills, corners, table-leg footprints, parquet seams.

Hotkeys in the calibration window
---------------------------------
- left click ............ add an image point
- ``u`` ................. undo last point
- ``r`` ................. reset all points
- ``c`` ................. compute homography from current points (>= 4)
- ``q`` / ``ESC`` ....... quit (saves only if you pressed ``c`` first)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


def _grab_rtsp_still(url: str, settle_seconds: float = 2.0) -> Optional[np.ndarray]:
    """Connect, drain a few frames, return the last one as a still."""

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:  # pragma: no cover - OpenCV build dependent
        pass
    if not cap.isOpened():
        cap.release()
        return None

    last: Optional[np.ndarray] = None
    deadline = time.time() + max(settle_seconds, 0.1)
    while time.time() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None:
            last = frame
        else:
            time.sleep(0.05)
    cap.release()
    return last


def _load_image(path: Path) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"could not read image: {path}")
    return img


def _ask_world_point(idx: int, click_uv: tuple[int, int]) -> Optional[tuple[float, float]]:
    print(
        f"\n[calibrate] image point #{idx + 1} at u={click_uv[0]} v={click_uv[1]}",
        flush=True,
    )
    print(
        "[calibrate] enter floor-plan mm for that point as 'x_mm y_mm' (or blank to cancel):",
        flush=True,
    )
    raw = sys.stdin.readline()
    if not raw:
        return None
    parts = raw.replace(",", " ").split()
    if len(parts) < 2:
        print("[calibrate] need two numbers; skipping.", file=sys.stderr)
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        print("[calibrate] could not parse numbers; skipping.", file=sys.stderr)
        return None


def _draw_overlay(
    base: np.ndarray,
    image_points: list[tuple[int, int]],
    world_points: list[tuple[float, float]],
) -> np.ndarray:
    canvas = base.copy()
    for i, (uv, mm) in enumerate(zip(image_points, world_points)):
        cv2.circle(canvas, uv, 6, (0, 200, 255), -1)
        cv2.circle(canvas, uv, 12, (0, 0, 0), 2)
        label = f"#{i + 1} ({mm[0]:.0f},{mm[1]:.0f})"
        cv2.putText(
            canvas,
            label,
            (uv[0] + 12, uv[1] - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 200, 255),
            2,
        )
    hint = (
        f"points={len(image_points)}  click=add  u=undo  r=reset  c=compute  q=quit"
    )
    cv2.putText(
        canvas,
        hint,
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return canvas


def _compute_homography(
    image_points: list[tuple[int, int]],
    world_points: list[tuple[float, float]],
) -> tuple[np.ndarray, float]:
    if len(image_points) < 4:
        raise ValueError("need at least 4 correspondences")
    src = np.array(image_points, dtype=np.float64)
    dst = np.array(world_points, dtype=np.float64)
    H, inliers = cv2.findHomography(src, dst, method=cv2.RANSAC, ransacReprojThreshold=8.0)
    if H is None:
        raise RuntimeError("findHomography failed; try clicking more points spread across the floor")

    # Reproject for a residual statistic the operator can sanity-check.
    src_h = np.hstack([src, np.ones((src.shape[0], 1), dtype=np.float64)])
    proj = (H @ src_h.T).T
    proj = proj[:, :2] / proj[:, 2:3]
    residuals = np.linalg.norm(proj - dst, axis=1)
    return H, float(np.mean(residuals))


def _merge_into_json(
    out_path: Path,
    cam_id: str,
    H: np.ndarray,
    floor_bounds_mm: dict[str, float],
    calib_image_size: tuple[int, int],
    image_points: list[tuple[int, int]],
    world_points: list[tuple[float, float]],
) -> None:
    if out_path.is_file():
        existing: dict[str, Any] = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        existing = {}

    existing[cam_id] = {
        "mode": "homography",
        "floor_bounds_mm": floor_bounds_mm,
        "H": [[float(v) for v in row] for row in H.tolist()],
        "calib_image_width": int(calib_image_size[0]),
        "calib_image_height": int(calib_image_size[1]),
        "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "image_points": [[int(u), int(v)] for u, v in image_points],
        "world_points_mm": [[float(x), float(y)] for x, y in world_points],
        "_comment": "Generated by tools/calibrate_homography.py — do not hand-edit H.",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _floor_bounds_for_camera(layout_path: Optional[Path], cam_id: str) -> dict[str, float]:
    """Read the trackable polygon for the camera's room; return its bounding box."""

    fallback = {"x_min": 0.0, "x_max": 19800.0, "y_min": 0.0, "y_max": 10200.0}
    if layout_path is None or not layout_path.is_file():
        return fallback
    data: dict[str, Any] = json.loads(layout_path.read_text(encoding="utf-8"))
    cams = data.get("cameras") or []
    cam_row = next((c for c in cams if c.get("name") == cam_id), None)
    if cam_row is None:
        return fallback
    room = str(cam_row.get("room", "")).strip()
    rooms = data.get("rooms") or {}
    rdata = rooms.get(room) or {}
    poly = rdata.get("trackable_polygon_mm")
    if not poly:
        return fallback
    xs = [float(p[0]) for p in poly]
    ys = [float(p[1]) for p in poly]
    return {
        "x_min": min(xs),
        "x_max": max(xs),
        "y_min": min(ys),
        "y_max": max(ys),
    }


def run(args: argparse.Namespace) -> int:
    if args.image is None and not args.rtsp:
        print("[calibrate] must provide --image FILE or --rtsp URL", file=sys.stderr)
        return 2

    if args.image is not None:
        frame = _load_image(args.image)
    else:
        print(f"[calibrate] grabbing still from {args.rtsp} ...", file=sys.stderr)
        frame = _grab_rtsp_still(args.rtsp, settle_seconds=args.rtsp_settle_sec)
        if frame is None:
            print("[calibrate] RTSP grab failed; check the URL", file=sys.stderr)
            return 2

    h, w = frame.shape[:2]
    image_points: list[tuple[int, int]] = []
    world_points: list[tuple[float, float]] = []

    win = f"calibrate:{args.camera}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, min(1280, w), min(960, h))

    pending_click: list[tuple[int, int]] = []

    def on_mouse(event, x, y, _flags, _param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            pending_click.append((int(x), int(y)))

    cv2.setMouseCallback(win, on_mouse)
    H_done: Optional[np.ndarray] = None
    residual: Optional[float] = None

    try:
        while True:
            if pending_click:
                uv = pending_click.pop(0)
                world = _ask_world_point(len(image_points), uv)
                if world is not None:
                    image_points.append(uv)
                    world_points.append(world)
                    print(
                        f"[calibrate] kept #{len(image_points)}: "
                        f"image=({uv[0]},{uv[1]})  world=({world[0]:.0f},{world[1]:.0f}) mm"
                    )

            overlay = _draw_overlay(frame, image_points, world_points)
            cv2.imshow(win, overlay)
            key = cv2.waitKey(50) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("u") and image_points:
                image_points.pop()
                world_points.pop()
                print("[calibrate] removed last point")
            elif key == ord("r"):
                image_points.clear()
                world_points.clear()
                print("[calibrate] reset")
            elif key == ord("c"):
                try:
                    H_done, residual = _compute_homography(image_points, world_points)
                except (ValueError, RuntimeError) as exc:
                    print(f"[calibrate] {exc}", file=sys.stderr)
                else:
                    n_pts = len(image_points)
                    print(
                        f"[calibrate] homography computed; mean reprojection residual "
                        f"= {residual:.1f} mm over {n_pts} points"
                    )
                    if n_pts == 4:
                        print(
                            "[calibrate] WARNING: with exactly 4 correspondences the "
                            "fit is mathematically exact (residual is always ~0 mm) "
                            "no matter how wrong the world coords are. Press 'r' and "
                            "re-pick with >= 6 well-spread points before relying on "
                            "this homography.",
                            file=sys.stderr,
                        )
                    elif residual > 200.0:
                        print(
                            "[calibrate] residual is large; consider re-clicking with "
                            "better-spread points before saving",
                            file=sys.stderr,
                        )
                    break
    finally:
        cv2.destroyWindow(win)

    if H_done is None:
        print("[calibrate] no homography computed; nothing written", file=sys.stderr)
        return 1

    floor_bounds = _floor_bounds_for_camera(args.layout, args.camera)
    _merge_into_json(
        out_path=args.out,
        cam_id=args.camera,
        H=H_done,
        floor_bounds_mm=floor_bounds,
        calib_image_size=(w, h),
        image_points=image_points,
        world_points=world_points,
    )
    print(
        f"[calibrate] wrote {args.camera} -> {args.out} "
        f"(residual={residual:.1f} mm, image={w}x{h})",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera", required=True, help="layout name (e.g. cam_kwz_sw)")
    ap.add_argument("--rtsp", default=None, help="RTSP URL to grab a still from")
    ap.add_argument("--image", type=Path, default=None, help="path to a saved still image")
    ap.add_argument(
        "--rtsp-settle-sec",
        type=float,
        default=2.0,
        help="seconds to drain RTSP frames before keeping the still",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("tracking_engine/calibration/camera_calibrations.json"),
        help="calibration JSON to merge into",
    )
    ap.add_argument(
        "--layout",
        type=Path,
        default=Path("camera_placement_plan/output/cameras_config.json"),
        help="cameras_config.json used to derive default floor_bounds_mm",
    )
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
