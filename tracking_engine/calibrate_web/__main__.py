"""CLI entry point for the calibration web tool.

Lets you run the UI on a laptop that is **not** on the camera LAN by
substituting each camera's RTSP feed with a local image or video file.
Same ``--video NAME=PATH`` semantics as
``python -m tracking_engine.multi_camera``.

Examples
--------
On-site (real RTSP feeds, default port 8090)::

    python -m tracking_engine.calibrate_web

Off-site full UI dry run using the saved stills::

    python -m tracking_engine.calibrate_web `
        --video cam_kwz_sw=stills/cam_kwz_sw.png `
        --video cam_kwz_nw=stills/cam_kwz_nw.png `
        --video cam_kwz_ne=stills/cam_kwz_ne.png `
        --video cam_kwz_se=stills/cam_kwz_se.png `
        --video cam_yoga_ne=stills/cam_yoga_ne.png `
        --video cam_yoga_se=stills/cam_yoga_se.png `
        --video cam_hallway_n=stills/cam_hallway_n.png

PNG/JPEG paths are recognised by extension and re-served as a static
frame; .mp4/.mkv paths are looped (rewinds to frame 0 at EOF).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _parse_video_overrides(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise argparse.ArgumentTypeError(
                f"expected NAME=path for --video, got {raw!r}"
            )
        name, _, path = raw.partition("=")
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise argparse.ArgumentTypeError(f"invalid --video token {raw!r}")
        out[name] = path
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=Path("tracking_engine/config.multi_camera.yaml"),
        help="multi-camera YAML (default: tracking_engine/config.multi_camera.yaml)",
    )
    ap.add_argument(
        "--video",
        dest="videos",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="replace RTSP for camera NAME with an image or video file (repeatable)",
    )
    ap.add_argument("--host", default="0.0.0.0", help="bind host (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8090, help="bind port (default 8090)")
    ap.add_argument(
        "--reload",
        action="store_true",
        help="dev mode: restart on code changes (useful when editing the UI)",
    )
    args = ap.parse_args()

    if not args.config.is_file():
        print(f"[calibrate_web] config not found: {args.config}", file=sys.stderr)
        return 2

    try:
        overrides = _parse_video_overrides(args.videos)
    except argparse.ArgumentTypeError as exc:
        print(f"[calibrate_web] {exc}", file=sys.stderr)
        return 2

    for name, p in overrides.items():
        if not Path(p).is_file():
            print(
                f"[calibrate_web] --video {name}={p}: file not found",
                file=sys.stderr,
            )
            return 2

    os.environ["TRACKING_CONFIG"] = str(args.config.resolve())
    if overrides:
        os.environ["CALIBRATE_WEB_VIDEO_OVERRIDES"] = json.dumps(overrides)
        print(
            f"[calibrate_web] using file overrides for: {sorted(overrides)}",
            file=sys.stderr,
        )
    else:
        os.environ.pop("CALIBRATE_WEB_VIDEO_OVERRIDES", None)

    try:
        import uvicorn
    except ImportError:
        print(
            "[calibrate_web] uvicorn missing — run:\n"
            "  python -m pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27'",
            file=sys.stderr,
        )
        return 2

    print(
        f"[calibrate_web] http://{args.host}:{args.port}  (config={args.config})",
        file=sys.stderr,
    )
    uvicorn.run(
        "tracking_engine.calibrate_web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
