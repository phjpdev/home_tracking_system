#!/usr/bin/env python3
"""Single-camera tracking MVP: RTSP -> detect -> ByteTrack -> homography -> HTTP POST."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import yaml

from .pipeline.detector import create_detector
from .pipeline.homography import foot_point_to_mm, load_calibration
from .pipeline.ingest import FrameSource, open_rtsp, open_video
from .pipeline.latency import LatencyMonitor
from .pipeline.poster import PositionPoster
from .pipeline.tracker_bytetrack import ByteTracker

ROOT = Path(__file__).resolve().parent


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_path(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (cfg_dir / path).resolve()


def run(cfg_path: Path, video_override: str | None) -> int:
    cfg = _load_yaml(cfg_path)
    cfg_dir = cfg_path.parent

    cam_cfg = cfg.get("camera", {})
    cam_id = str(cam_cfg.get("id", "cam_1"))
    rtsp_url = str(cam_cfg.get("rtsp_url", ""))

    calib_path = _resolve_path(cfg_dir, str(cfg.get("calibration_file", "calibration/camera_calibrations.json")))
    calibration = load_calibration(calib_path, cam_id)

    detector, det_cleanup = create_detector(cfg)
    tracker = ByteTracker()
    lat = LatencyMonitor()

    pcfg = cfg.get("poster", {})
    poster = PositionPoster(
        url=str(pcfg.get("url", "http://127.0.0.1:8765/tracking/positions")),
        timeout=float(pcfg.get("timeout_sec", 3.0)),
        dry_run=bool(pcfg.get("dry_run", False)),
        verify_tls=bool(pcfg.get("verify_tls", True)),
    )
    include_lat = bool(pcfg.get("include_latency_in_post", False))

    track_cfg = cfg.get("tracking", {})
    zone = str(track_cfg.get("zone", "unknown"))
    default_privacy = bool(track_cfg.get("default_privacy", False))

    rcfg = cfg.get("runtime", {})
    show_preview = bool(rcfg.get("show_preview", False))
    log_every = int(rcfg.get("latency_log_every_n_frames", 30))
    max_frames = int(rcfg.get("max_frames", 0))
    preview_scale = float(rcfg.get("preview_scale", 0.5))

    source: FrameSource
    if video_override:
        vp = Path(video_override).expanduser()
        if not vp.is_file():
            print(
                f"[engine] video file not found: {vp} "
                f"(use a real path, not the README placeholder)",
                file=sys.stderr,
            )
            return 2
        print(f"[engine] video file: {vp}", file=sys.stderr)
        try:
            source = open_video(str(vp))
        except OSError as e:
            print(f"[engine] {e}", file=sys.stderr)
            return 2
    else:
        if not rtsp_url:
            print("[engine] camera.rtsp_url empty and no --video", file=sys.stderr)
            return 2
        print(f"[engine] RTSP: {rtsp_url}", file=sys.stderr)
        source = open_rtsp(rtsp_url)

    print(
        f"[engine] detector={detector.backend()} calib={calibration.mode} "
        f"POST {poster.url} dry_run={poster.dry_run}",
        file=sys.stderr,
    )
    if not poster.dry_run:
        print(
            "[engine] ensure the sink is running (e.g. "
            "`python tracking_engine/mock_maro_server.py`) or set poster.dry_run: true",
            file=sys.stderr,
        )

    frame_i = 0
    post_fail_last_log_frame = -10**9
    post_refused_hint_shown = False
    try:
        while True:
            t_frame0 = time.perf_counter()

            t0 = time.perf_counter()
            ok, frame = source.read()
            lat.record("grab", (time.perf_counter() - t0) * 1000.0)
            if not ok or frame is None:
                print("[engine] end of stream or read failure", file=sys.stderr)
                break

            def rec_detect(ms: float) -> None:
                lat.record("detect", ms)

            t1 = time.perf_counter()
            detections = detector.detect(frame, latency_record=rec_detect)

            t2 = time.perf_counter()
            tracked = tracker.update(detections)
            lat.record("track", (time.perf_counter() - t2) * 1000.0)

            persons: list[dict[str, Any]] = []
            t_geom0 = time.perf_counter()
            fh, fw = frame.shape[:2]

            if tracked.tracker_id is None or len(tracked) == 0:
                lat.record("geom", (time.perf_counter() - t_geom0) * 1000.0)
            else:
                for i in range(len(tracked)):
                    tid = tracked.tracker_id[i]
                    xyxy = tracked.xyxy[i]
                    if tid is None:
                        continue
                    x1, y1, x2, y2 = [float(v) for v in xyxy]
                    foot_u = (x1 + x2) / 2.0
                    foot_v = y2
                    x_mm, y_mm = foot_point_to_mm(calibration, foot_u, foot_v, fw, fh)
                    persons.append(
                        {
                            "id": f"t{int(tid)}",
                            "x": int(round(x_mm)),
                            "y": int(round(y_mm)),
                            "zone": zone,
                            "privacy": default_privacy,
                        }
                    )
                lat.record("geom", (time.perf_counter() - t_geom0) * 1000.0)

            ts = time.time()
            payload: dict[str, Any] = {
                "cam_id": cam_id,
                "ts": round(ts, 3),
                "persons": persons,
            }
            if include_lat:
                payload["latency_ms"] = {k: round(v.last_ms, 3) for k, v in lat.stages.items()}

            t_post0 = time.perf_counter()
            ok_post, err = poster.post(payload)
            lat.record("post", (time.perf_counter() - t_post0) * 1000.0)

            lat.record("frame_total", (time.perf_counter() - t_frame0) * 1000.0)

            if ok_post:
                post_refused_hint_shown = False
            elif persons:
                log_gap = max(log_every, 30)
                if frame_i - post_fail_last_log_frame >= log_gap:
                    post_fail_last_log_frame = frame_i
                    print(f"[engine] POST failed: {err}", file=sys.stderr)
                    if not post_refused_hint_shown and (
                        "10061" in err
                        or "actively refused" in err
                        or "Connection refused" in err
                    ):
                        post_refused_hint_shown = True
                        print(
                            "[engine] connection refused: start the mock server on port 8765 "
                            "(see message above) or enable poster.dry_run in config.yaml",
                            file=sys.stderr,
                        )

            frame_i += 1
            if log_every > 0 and frame_i % log_every == 0:
                print(f"[engine] frame={frame_i}  {lat.summary_line()}", file=sys.stderr)

            if show_preview and persons:
                vis = frame.copy()
                for i in range(len(tracked)):
                    if tracked.tracker_id is None:
                        break
                    tid = tracked.tracker_id[i]
                    if tid is None:
                        continue
                    x1, y1, x2, y2 = [int(v) for v in tracked.xyxy[i]]
                    label = f"t{int(tid)}"
                    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 200, 0), 2)
                    cv2.putText(
                        vis,
                        label,
                        (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 200, 0),
                        2,
                    )
                ph, pw = vis.shape[:2]
                if preview_scale != 1.0:
                    vis = cv2.resize(
                        vis,
                        (int(pw * preview_scale), int(ph * preview_scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                cv2.imshow("tracking_engine", vis)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            if max_frames > 0 and frame_i >= max_frames:
                break
    finally:
        source.release()
        if show_preview:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        poster.close()
        if det_cleanup:
            det_cleanup()

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.yaml",
        help="path to config.yaml",
    )
    ap.add_argument(
        "--video",
        help="override RTSP with a local video file (development / replay)",
    )
    args = ap.parse_args()

    if not args.config.is_file():
        print(f"[engine] missing config: {args.config}", file=sys.stderr)
        return 2

    return run(args.config.resolve(), args.video)


if __name__ == "__main__":
    raise SystemExit(main())
