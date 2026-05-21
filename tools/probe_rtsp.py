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
        out.append({"name": name, "url": url})
    return out


def _probe_one(name: str, url: str, frames: int, timeout: float, save_dir: Path | None) -> dict[str, Any]:
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
        out_path = save_dir / f"{name}.png"
        cv2.imwrite(str(out_path), last_frame)

    return {
        "name": name,
        "url": url,
        "ok": True,
        "frames": decoded,
        "fps": decoded / elapsed,
        "width": fw,
        "height": fh,
        "elapsed_sec": elapsed,
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
    args = ap.parse_args()

    if not args.config.is_file():
        print(f"[probe] config not found: {args.config}", file=sys.stderr)
        return 2

    streams = _load_streams(args.config)
    if not streams:
        print("[probe] no enabled streams in config", file=sys.stderr)
        return 2

    print(f"[probe] checking {len(streams)} enabled stream(s) from {args.config}", file=sys.stderr)
    results: list[dict[str, Any]] = []
    for s in streams:
        print(f"[probe] -> {s['name']}  {s['url']}", file=sys.stderr)
        r = _probe_one(s["name"], s["url"], args.frames, args.timeout, args.save_stills)
        results.append(r)
        if r.get("ok"):
            print(
                f"   OK  frames={r['frames']:>3d}  size={r['width']}x{r['height']}  "
                f"fps~{r['fps']:.1f}  in {r['elapsed_sec']:.1f}s"
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
