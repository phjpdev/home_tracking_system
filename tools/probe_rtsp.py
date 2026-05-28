#!/usr/bin/env python3
"""Probe each enabled RTSP stream and report whether the Pi can decode it.

Reads the same ``tracking_engine/config.multi_camera.yaml`` the runtime uses,
opens every ``enabled: true`` camera, and prints frame size, decode FPS,
and a pass/fail flag per stream. Useful as a first-step sanity check after
plugging the cameras in.

Usage
-----
.. code-block:: bash

   python tools/probe_rtsp.py
   python tools/probe_rtsp.py --frames 60 --timeout 8
   python tools/probe_rtsp.py --save-stills stills/   # save one PNG per camera
   python tools/probe_rtsp.py --camera cam_hallway_n --save-stills /tmp/hallway_test

Exit code is 0 iff every enabled stream produced at least one frame within
``--timeout`` seconds.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import yaml


_ALLOWED_ROTATIONS = (0, 90, 180, 270)


def _norm_rotate(raw: Any) -> int:
    if raw is None:
        return 0
    try:
        deg = int(raw) % 360
    except (TypeError, ValueError):
        return 0
    return deg if deg in _ALLOWED_ROTATIONS else 0


def _apply_rotate(frame, deg: int):
    if deg == 0 or frame is None:
        return frame
    if deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def _load_streams(cfg_path: Path) -> list[dict[str, Any]]:
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    mc = cfg.get("multi_camera") or {}
    streams = mc.get("streams") or []
    out: list[dict[str, Any]] = []
    for row in streams:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("enabled", False)):
            continue
        name = str(row.get("name", "")).strip()
        url = str(row.get("rtsp_url", "")).strip()
        if not name or not url:
            continue
        out.append(
            {
                "name": name,
                "url": url,
                "rotate": _norm_rotate(row.get("rotate")),
            }
        )
    return out


def _probe_one(
    name: str,
    url: str,
    frames: int,
    timeout: float,
    save_dir: Path | None,
    rotate_deg: int,
) -> dict[str, Any]:
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    if not cap.isOpened():
        return {"name": name, "url": url, "ok": False, "error": "cap not opened"}

    started = time.time()
    deadline = started + max(timeout, 0.5)
    decoded = 0
    last_frame = None
    fw = fh = 0
    while time.time() < deadline and decoded < frames:
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.02)
            continue
        decoded += 1
        last_frame = frame
        fh, fw = frame.shape[:2]

    elapsed = max(time.time() - started, 1e-3)
    cap.release()

    if decoded == 0:
        return {
            "name": name,
            "url": url,
            "ok": False,
            "error": f"no frame decoded in {timeout:.1f}s",
        }

    if save_dir is not None and last_frame is not None:
        save_dir.mkdir(parents=True, exist_ok=True)
        rotated = _apply_rotate(last_frame, rotate_deg) if rotate_deg else last_frame
        out_path = save_dir / f"{name}.png"
        cv2.imwrite(str(out_path), rotated)

    return {
        "name": name,
        "url": url,
        "ok": True,
        "frames": decoded,
        "fps": decoded / elapsed,
        "width": fw,
        "height": fh,
        "elapsed_sec": elapsed,
        "rotate_deg": rotate_deg,
        "saved": str(save_dir / f"{name}.png") if save_dir else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("tracking_engine/config.multi_camera.yaml"),
        help="multi-camera YAML to read enabled streams from",
    )
    ap.add_argument("--frames", type=int, default=30, help="frames to decode per camera before stopping")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds per camera before giving up")
    ap.add_argument(
        "--save-stills",
        type=Path,
        default=None,
        help="optional directory to save one still per camera (PNG)",
    )
    ap.add_argument(
        "--no-rotate",
        action="store_true",
        help=(
            "ignore the per-camera 'rotate' field and save the raw stream frame; "
            "use this to verify whether a rotation override is actually needed"
        ),
    )
    ap.add_argument(
        "--camera",
        action="append",
        default=[],
        metavar="NAME",
        help="probe only these camera name(s); repeat for multiple (default: all enabled)",
    )
    args = ap.parse_args()

    if not args.config.is_file():
        print(f"[probe] config not found: {args.config}", file=sys.stderr)
        return 2

    streams = _load_streams(args.config)
    if args.camera:
        want = {str(n).strip() for n in args.camera if str(n).strip()}
        streams = [s for s in streams if s["name"] in want]
        if not streams:
            print(f"[probe] no enabled stream matched --camera {sorted(want)}", file=sys.stderr)
            return 2
    if not streams:
        print("[probe] no enabled streams in config", file=sys.stderr)
        return 2

    print(f"[probe] checking {len(streams)} stream(s) from {args.config}", file=sys.stderr)
    results: list[dict[str, Any]] = []
    for s in streams:
        rot = 0 if args.no_rotate else int(s.get("rotate") or 0)
        rot_note = f"  rotate={rot}deg" if rot else ""
        print(f"[probe] -> {s['name']}  {s['url']}{rot_note}", file=sys.stderr)
        r = _probe_one(
            s["name"],
            s["url"],
            args.frames,
            args.timeout,
            args.save_stills,
            rot,
        )
        results.append(r)
        if r.get("ok"):
            print(
                f"   OK  frames={r['frames']:>3d}  size={r['width']}x{r['height']}  "
                f"fps~{r['fps']:.1f}  in {r['elapsed_sec']:.1f}s"
                + (f"  rotate={r.get('rotate_deg')}deg" if r.get("rotate_deg") else "")
                + (f"  still={r['saved']}" if r.get("saved") else "")
            )
        else:
            print(f"   FAIL  {r.get('error')}")

    bad = [r["name"] for r in results if not r.get("ok")]
    print()
    print(f"[probe] summary: {len(results) - len(bad)}/{len(results)} OK")
    if bad:
        print(f"[probe] failed cameras: {bad}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
