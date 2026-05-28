#!/usr/bin/env python3
"""Multi-camera tracking: N streams -> shared detector -> per-camera ByteTrack -> floor mm -> POST per camera."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import yaml

from .observability import configure_logging, metrics, start_metrics_server
from .pipeline.cameras_layout import load_cameras_layout, resolve_active_streams
from .pipeline.detector import create_detector
from .pipeline.homography import (
    COORD_MARO_PLAN_PX,
    foot_point_to_mm,
    foot_point_to_plan_px,
    load_calibration,
)
from .pipeline.plan_fusion import PlanFusionConfig, PlanFusionCoordinator, zones_from_overlay
from .pipeline.ingest import FrameSource, open_rtsp, open_rtsp_latest, open_video
from .pipeline.latency import LatencyMonitor
from .pipeline.poster import PositionPoster
from .pipeline.tracker_bytetrack import ByteTracker
from .reid import ReidConfig, ReIDCoordinator

ROOT = Path(__file__).resolve().parent


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_path(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (cfg_dir / path).resolve()


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


def _open_source(
    *,
    rtsp_url: str,
    video_path: Optional[str],
    use_latest_rtsp: bool,
) -> FrameSource:
    if video_path:
        vp = Path(video_path).expanduser()
        if not vp.is_file():
            raise FileNotFoundError(f"video file not found: {vp}")
        return open_video(str(vp))
    if use_latest_rtsp:
        return open_rtsp_latest(rtsp_url)
    return open_rtsp(rtsp_url)


def _apply_rotate(frame, deg: int):
    """Compensate for cameras mounted upside-down or rotated. ``deg`` is one of 0/90/180/270."""

    if deg == 0 or frame is None:
        return frame
    if deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def run(cfg_path: Path, video_overrides: dict[str, str]) -> int:
    cfg = _load_yaml(cfg_path)
    cfg_dir = cfg_path.parent

    obs_cfg = cfg.get("observability", {}) or {}
    configure_logging(
        level=str(obs_cfg.get("log_level", "INFO")),
        log_dir=obs_cfg.get("log_dir"),
        json=obs_cfg.get("log_json"),
    )
    if bool(obs_cfg.get("metrics_enabled", False)):
        start_metrics_server(
            port=int(obs_cfg.get("metrics_port", 9100)),
            addr=str(obs_cfg.get("metrics_bind", "0.0.0.0")),
        )

    mc = cfg.get("multi_camera") or {}
    layout_rel = str(mc.get("cameras_layout_file", "")).strip()
    if not layout_rel:
        print("[multi] multi_camera.cameras_layout_file missing in config", file=sys.stderr)
        return 2
    layout_path = _resolve_path(cfg_dir, layout_rel)
    if not layout_path.is_file():
        print(f"[multi] cameras layout not found: {layout_path}", file=sys.stderr)
        return 2

    streams_yaml = mc.get("streams")
    if not isinstance(streams_yaml, list) or not streams_yaml:
        print("[multi] multi_camera.streams must be a non-empty list", file=sys.stderr)
        return 2

    layout = load_cameras_layout(layout_path)
    try:
        cameras = resolve_active_streams(layout, streams_yaml, video_overrides)
    except (KeyError, ValueError) as e:
        print(f"[multi] {e}", file=sys.stderr)
        return 2

    if not cameras:
        print("[multi] no enabled cameras after resolving streams", file=sys.stderr)
        return 2

    calib_path = _resolve_path(cfg_dir, str(cfg.get("calibration_file", "calibration/camera_calibrations.json")))
    calibrations = []
    trackers: list[ByteTracker] = []
    sources: list[FrameSource] = []
    rotations: list[int] = []

    use_latest_rtsp = bool(mc.get("use_latest_frame_rtsp", True))

    try:
        for cam in cameras:
            calibrations.append(load_calibration(calib_path, cam["name"]))
            trackers.append(ByteTracker())
            sources.append(
                _open_source(
                    rtsp_url=cam["rtsp_url"],
                    video_path=cam.get("video_path"),
                    use_latest_rtsp=use_latest_rtsp,
                )
            )
            rotations.append(int(cam.get("rotate", 0) or 0))
    except (KeyError, OSError, FileNotFoundError) as e:
        print(f"[multi] setup failed: {e}", file=sys.stderr)
        for s in sources:
            try:
                s.release()
            except Exception:
                pass
        return 2

    detector, det_cleanup = create_detector(cfg)

    pcfg = cfg.get("poster", {})
    use_plan_px = False
    plan_w_px = plan_h_px = None
    if calibrations:
        use_plan_px = calibrations[0].coordinate_space == COORD_MARO_PLAN_PX
        if use_plan_px and calibrations[0].plan_size_px:
            plan_w_px, plan_h_px = calibrations[0].plan_size_px
    poster = PositionPoster(
        url=str(pcfg.get("url", "http://127.0.0.1:8765/tracking/positions")),
        timeout=float(pcfg.get("timeout_sec", 3.0)),
        dry_run=bool(pcfg.get("dry_run", False)),
        verify_tls=bool(pcfg.get("verify_tls", True)),
        plan_w_px=plan_w_px,
        plan_h_px=plan_h_px,
    )
    include_lat = bool(pcfg.get("include_latency_in_post", False))

    track_defaults = cfg.get("tracking", {}) or {}
    default_privacy = bool(track_defaults.get("default_privacy", False))
    zone_fallback = str(track_defaults.get("zone", "unknown"))

    fusion_cfg_raw = track_defaults.get("plan_fusion") or {}
    fusion_enabled = bool(fusion_cfg_raw.get("enabled", use_plan_px))
    plan_fusion: Optional[PlanFusionCoordinator] = None
    if fusion_enabled:
        zones: list = []
        maro_cfg = cfg.get("maro") or {}
        cache_rel = str(maro_cfg.get("assets_cache_dir") or "calibration/maro_cache")
        cache_dir = _resolve_path(cfg_dir, cache_rel)
        try:
            from .pipeline.maro_floorplan import load_maro_floorplan

            api_base = str(maro_cfg.get("api_base") or "http://192.168.178.25:8420")
            assets = load_maro_floorplan(api_base, cache_dir)
            zones = zones_from_overlay(assets.overlay_for_ui())
        except Exception as exc:
            print(f"[multi] plan_fusion: could not load Maro zones ({exc})", file=sys.stderr)
        plan_fusion = PlanFusionCoordinator(
            PlanFusionConfig(
                enabled=True,
                cluster_distance_px=float(fusion_cfg_raw.get("cluster_distance_px", 80.0)),
                ema_alpha=float(fusion_cfg_raw.get("ema_alpha", 0.35)),
                ema_reset_gap_sec=float(fusion_cfg_raw.get("ema_reset_gap_sec", 2.0)),
                drop_outside_zones=bool(fusion_cfg_raw.get("drop_outside_zones", True)),
            ),
            zones,
        )
    fused_post = bool((cfg.get("poster") or {}).get("fused_mode", fusion_enabled))

    rcfg = cfg.get("runtime", {})
    show_preview = bool(rcfg.get("show_preview", False))
    preview_cam = rcfg.get("show_preview_camera")
    preview_cam_name = str(preview_cam).strip() if preview_cam else ""
    log_every = int(rcfg.get("latency_log_every_n_frames", 30))
    max_ticks = int(rcfg.get("max_ticks", rcfg.get("max_frames", 0)))

    coord_note = "maro_plan_px" if use_plan_px else "legacy_mm"
    fusion_note = " fused_post" if fused_post else ""
    print(
        f"[multi] cameras={len(cameras)} layout={layout_path.name} "
        f"coords={coord_note}{fusion_note} "
        f"detector={detector.backend()} POST={poster.url} dry_run={poster.dry_run}",
        file=sys.stderr,
    )
    rotated = [(c["name"], r) for c, r in zip(cameras, rotations) if r]
    if rotated:
        print(
            "[multi] rotation overrides: "
            + ", ".join(f"{n}={r}deg" for n, r in rotated),
            file=sys.stderr,
        )
    if not poster.dry_run:
        print(
            "[multi] ensure sink is running (`python tracking_engine/mock_maro_server.py`) "
            "or set poster.dry_run: true",
            file=sys.stderr,
        )

    reid_cfg = ReidConfig.from_cfg(cfg, cfg_dir)
    reid: Optional[ReIDCoordinator] = None
    if reid_cfg.enabled:
        try:
            reid = ReIDCoordinator(reid_cfg)
        except Exception as exc:
            print(f"[multi] re-id disabled: init failed ({exc})", file=sys.stderr)
        else:
            backend = reid.embedder_backend()
            print(
                f"[multi] re-id on embedder={backend} gallery={reid_cfg.sqlite_path}",
                file=sys.stderr,
            )
            if backend == "fallback_opencv":
                print(
                    "[multi] WARNING: re-id is using the grayscale fallback embedder; "
                    "global IDs will be unreliable. Export OSNet to ONNX via "
                    "tools/export_osnet_onnx.py and set reid.body.onnx_model_path.",
                    file=sys.stderr,
                )
            if reid_cfg.face_enabled:
                print(
                    "[multi] reid.face.enabled is True but facial recognition fusion is "
                    "not wired yet — body embeddings only.",
                    file=sys.stderr,
                )

    tick = 0
    post_fail_last_log_tick = -10**9
    post_refused_hint_shown = False
    lat_all = LatencyMonitor()

    preview_idx = 0
    if preview_cam_name:
        names_list = [c["name"] for c in cameras]
        if preview_cam_name not in names_list:
            print(
                f"[multi] show_preview_camera {preview_cam_name!r} not in active set {names_list}",
                file=sys.stderr,
            )
        else:
            preview_idx = names_list.index(preview_cam_name)

    try:
        while True:
            if reid is not None:
                reid.begin_tick()
            t_tick0 = time.perf_counter()
            ts = time.time()
            sum_grab = sum_detect = sum_track = sum_geom = 0.0
            had_any_frame = False
            first_post_err: str | None = None
            pending_payloads: list[dict[str, Any]] = []
            fusion_rows: list[dict[str, Any]] = []
            per_cam_detections: dict[str, int] = {c["name"]: 0 for c in cameras}
            video_eof_stop = False

            for idx, cam in enumerate(cameras):
                cam_id = cam["name"]
                zone = str(cam.get("zone") or zone_fallback)
                calibration = calibrations[idx]
                tracker = trackers[idx]
                source = sources[idx]
                is_file = bool(cam.get("video_path"))

                t_grab = time.perf_counter()
                ok, frame = source.read()
                if ok and frame is not None and rotations[idx]:
                    frame = _apply_rotate(frame, rotations[idx])
                sum_grab += (time.perf_counter() - t_grab) * 1000.0

                if not ok or frame is None:
                    if is_file:
                        print(f"[multi] end of video file: {cam_id}", file=sys.stderr)
                        video_eof_stop = True
                        break
                    continue

                had_any_frame = True

                t_det = time.perf_counter()
                detections = detector.detect(frame, latency_record=None)
                sum_detect += (time.perf_counter() - t_det) * 1000.0

                t_tr = time.perf_counter()
                tracked = tracker.update(detections)
                sum_track += (time.perf_counter() - t_tr) * 1000.0

                persons: list[dict[str, Any]] = []
                t_geom0 = time.perf_counter()
                fh, fw = frame.shape[:2]

                extra_by_tid: dict[int, dict[str, Any]] = {}
                if tracked.tracker_id is None or len(tracked) == 0:
                    sum_geom += (time.perf_counter() - t_geom0) * 1000.0
                else:
                    for i in range(len(tracked)):
                        tid = tracked.tracker_id[i]
                        xyxy = tracked.xyxy[i]
                        if tid is None:
                            continue
                        x1, y1, x2, y2 = [float(v) for v in xyxy]
                        foot_u = (x1 + x2) / 2.0
                        foot_v = y2
                        if calibration.coordinate_space == COORD_MARO_PLAN_PX:
                            px_x, px_y = foot_point_to_plan_px(
                                calibration, foot_u, foot_v, fw, fh
                            )
                        else:
                            px_x, px_y = foot_point_to_mm(
                                calibration, foot_u, foot_v, fw, fh
                            )
                        person_row: dict[str, Any] = {
                            "id": f"t{int(tid)}",
                            "x": int(round(px_x)),
                            "y": int(round(px_y)),
                            "zone": zone,
                            "privacy": default_privacy,
                        }
                        if reid is not None:
                            rex = reid.observe(
                                cam_id=cam_id,
                                frame_ts=float(ts),
                                frame_bgr=frame,
                                xyxy=(x1, y1, x2, y2),
                                tracker_id=int(tid),
                            )
                            extra_by_tid[int(tid)] = rex
                            person_row.update(rex)
                        persons.append(person_row)
                        if fused_post and plan_fusion is not None:
                            fusion_rows.append(
                                {
                                    "cam_id": cam_id,
                                    "id": person_row["id"],
                                    "x": person_row["x"],
                                    "y": person_row["y"],
                                    "privacy": person_row["privacy"],
                                    "global_id": person_row.get("global_id"),
                                }
                            )
                    sum_geom += (time.perf_counter() - t_geom0) * 1000.0

                if not fused_post:
                    pending_payloads.append(
                        {
                            "cam_id": cam_id,
                            "ts": round(ts, 3),
                            "persons": persons,
                        }
                    )

                if show_preview and idx == preview_idx and persons:
                    vis = frame.copy()
                    if tracked.tracker_id is not None:
                        for i in range(len(tracked)):
                            tid = tracked.tracker_id[i]
                            if tid is None:
                                continue
                            x1, y1, x2, y2 = [int(v) for v in tracked.xyxy[i]]
                            label = f"t{int(tid)}"
                            ex = extra_by_tid.get(int(tid))
                            gid = None
                            if ex is not None:
                                gid = ex.get("global_id")
                            if gid is not None:
                                label = f"{label}:{str(gid)[:8]}"
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
                    preview_scale = float(rcfg.get("preview_scale", 0.5))
                    ph, pw = vis.shape[:2]
                    if preview_scale != 1.0:
                        vis = cv2.resize(
                            vis,
                            (int(pw * preview_scale), int(ph * preview_scale)),
                            interpolation=cv2.INTER_AREA,
                        )
                    cv2.imshow("tracking_engine_multi", vis)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        raise KeyboardInterrupt

            if reid is not None:
                reid.prune_stale(ts)

            if video_eof_stop:
                break

            if fused_post and plan_fusion is not None:
                fused_persons = plan_fusion.fuse_tick(fusion_rows, ts)
                pending_payloads = [
                    {
                        "cam_id": "fused",
                        "ts": round(ts, 3),
                        "persons": fused_persons,
                    }
                ]
            elif fused_post and fusion_rows:
                pending_payloads = [
                    {
                        "cam_id": cameras[0]["name"] if cameras else "fused",
                        "ts": round(ts, 3),
                        "persons": fusion_rows,
                    }
                ]

            lat_all.record("grab", sum_grab)
            lat_all.record("detect", sum_detect)
            lat_all.record("track", sum_track)
            lat_all.record("geom", sum_geom)
            metrics.inference_latency_ms.labels(stage="grab").observe(sum_grab)
            metrics.inference_latency_ms.labels(stage="detect").observe(sum_detect)
            metrics.inference_latency_ms.labels(stage="track").observe(sum_track)
            metrics.inference_latency_ms.labels(stage="geom").observe(sum_geom)

            sum_post = 0.0
            latency_snapshot: dict[str, float] | None = None
            if include_lat:
                latency_snapshot = {
                    k: round(lat_all.stages[k].last_ms, 3)
                    for k in ("grab", "detect", "track", "geom")
                    if k in lat_all.stages
                }

            for payload in pending_payloads:
                if include_lat and latency_snapshot is not None:
                    payload["latency_ms"] = dict(latency_snapshot)
                t_post0 = time.perf_counter()
                ok_post, err = poster.post(payload)
                sum_post += (time.perf_counter() - t_post0) * 1000.0
                if not ok_post and payload.get("persons"):
                    metrics.post_failures.labels(endpoint="positions").inc()
                    if first_post_err is None:
                        first_post_err = err or "unknown error"
                for p in payload.get("persons", []):
                    if "reid_score" in p:
                        metrics.body_match_score.observe(float(p["reid_score"]))
                    if "face_score" in p:
                        metrics.face_match_score.observe(float(p["face_score"]))

            lat_all.record("post", sum_post)
            lat_all.record("frame_total", (time.perf_counter() - t_tick0) * 1000.0)
            metrics.inference_latency_ms.labels(stage="post").observe(sum_post)
            metrics.inference_latency_ms.labels(stage="frame_total").observe(
                (time.perf_counter() - t_tick0) * 1000.0
            )

            if first_post_err is not None:
                log_gap = max(log_every, 30)
                if tick - post_fail_last_log_tick >= log_gap:
                    post_fail_last_log_tick = tick
                    print(f"[multi] POST failed: {first_post_err}", file=sys.stderr)
                    if not post_refused_hint_shown and (
                        "10061" in first_post_err
                        or "actively refused" in first_post_err
                        or "Connection refused" in first_post_err
                    ):
                        post_refused_hint_shown = True
                        is_local = (
                            "127.0.0.1" in poster.url
                            or "localhost" in poster.url
                        )
                        if is_local:
                            hint = (
                                "[multi] connection refused on localhost: start "
                                "mock_maro_server.py or set poster.dry_run: true"
                            )
                        else:
                            hint = (
                                f"[multi] connection refused at {poster.url}: the "
                                "configured Maro server is not reachable. Verify it "
                                "is running and accepting connections; the engine "
                                "keeps detecting and will deliver POSTs as soon as "
                                "the server is back."
                            )
                        print(hint, file=sys.stderr)

            tick += 1
            if log_every > 0 and tick % log_every == 0:
                tick_persons = sum(len(p.get("persons") or []) for p in pending_payloads)
                tick_active = sum(
                    1 for p in pending_payloads if p.get("persons")
                )
                # Per-cam counts for ALL cameras (incl. 0) so we can spot a cam that never detects
                per_cam: dict[str, int] = {c["name"]: 0 for c in cameras}
                for p in pending_payloads:
                    cid = p.get("cam_id")
                    if cid in per_cam:
                        per_cam[cid] = len(p.get("persons") or [])
                breakdown = " ".join(
                    f"{name.replace('cam_', '')}={n}" for name, n in per_cam.items()
                )
                print(
                    f"[multi] tick={tick} persons={tick_persons} "
                    f"active={tick_active}/{len(cameras)} | {breakdown} | "
                    f"{lat_all.summary_line()}",
                    file=sys.stderr,
                )

            if max_ticks > 0 and tick >= max_ticks:
                break

            if not had_any_frame:
                time.sleep(0.02)

    except KeyboardInterrupt:
        print("[multi] interrupted", file=sys.stderr)
    finally:
        for s in sources:
            try:
                s.release()
            except Exception:
                pass
        if show_preview:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        poster.close()
        if reid is not None:
            reid.close()
        if det_cleanup:
            det_cleanup()

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.multi_camera.yaml",
        help="path to multi-camera config YAML",
    )
    ap.add_argument(
        "--video",
        dest="videos",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="override RTSP for camera NAME with a video file (repeatable)",
    )
    args = ap.parse_args()

    if not args.config.is_file():
        print(f"[multi] missing config: {args.config}", file=sys.stderr)
        return 2

    try:
        vmap = _parse_video_overrides(args.videos)
    except argparse.ArgumentTypeError as e:
        print(f"[multi] {e}", file=sys.stderr)
        return 2

    return run(args.config.resolve(), vmap)


if __name__ == "__main__":
    raise SystemExit(main())