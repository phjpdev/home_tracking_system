"""FastAPI app: multi-camera homography calibration UI.

See :mod:`tracking_engine.calibrate_web` for the workflow rationale.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import FastAPI, HTTPException, Query, Response
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "fastapi + uvicorn are required for the calibration UI:\n"
        "  pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27'"
    ) from exc

import yaml

from ..pipeline.cameras_layout import load_cameras_layout, resolve_active_streams
from .calibration_io import atomic_merge_calibrations, build_camera_entry, read_calibrations
from .homography_fit import Position, fit_all
from ..pipeline.camera_undistort import CameraUndistortRegistry
from ..pipeline.maro_floorplan import COORDINATE_SPACE, load_maro_floorplan
from .snapshot import CameraSnapshotPool
from .led_marker import LedMarker
from ..calibration.auto_led import LedAutoCalConfig, LedAutoCalibrator

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

_CONFIG_PATH = Path(
    os.environ.get(
        "TRACKING_CONFIG",
        "tracking_engine/config.multi_camera.yaml",
    )
)


def _resolve(cfg_dir: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (cfg_dir / path).resolve()


def _load_runtime_config() -> dict[str, Any]:
    """Return resolved runtime context: cameras, paths, floor-plan meta."""

    if not _CONFIG_PATH.is_file():
        raise FileNotFoundError(
            f"TRACKING_CONFIG not found: {_CONFIG_PATH}. Set the env var or "
            "run from the repo root."
        )
    with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg_dir = _CONFIG_PATH.parent

    mc = cfg.get("multi_camera") or {}
    layout_rel = str(mc.get("cameras_layout_file") or "").strip()
    if not layout_rel:
        raise ValueError("multi_camera.cameras_layout_file missing in config")
    layout_path = _resolve(cfg_dir, layout_rel)
    layout = load_cameras_layout(layout_path)

    streams = mc.get("streams") or []
    overrides_raw = os.environ.get("CALIBRATE_WEB_VIDEO_OVERRIDES") or ""
    video_overrides: dict[str, str] = {}
    if overrides_raw:
        try:
            video_overrides = dict(json.loads(overrides_raw))
        except (ValueError, TypeError) as exc:
            logger.warning("invalid CALIBRATE_WEB_VIDEO_OVERRIDES=%r: %s", overrides_raw, exc)
    cameras = resolve_active_streams(layout, streams, video_overrides)
    if video_overrides:
        logger.info(
            "calibrate_web: using file overrides for %s",
            sorted(video_overrides.keys()),
        )

    calib_path = _resolve(
        cfg_dir, str(cfg.get("calibration_file", "calibration/camera_calibrations.json"))
    )
    intrinsics_rel = str(cfg.get("intrinsics_file") or "calibration/camera_intrinsics.json")
    intrinsics_path = _resolve(cfg_dir, intrinsics_rel)

    maro_cfg = cfg.get("maro") or {}
    api_base = str(
        os.environ.get("MARO_API_BASE")
        or maro_cfg.get("api_base")
        or "http://127.0.0.1:8420"
    ).strip()
    cache_rel = str(maro_cfg.get("assets_cache_dir") or "calibration/maro_cache")
    cache_dir = _resolve(cfg_dir, cache_rel)
    save_residual_px_max = float((cfg.get("calibration") or {}).get("save_residual_px_max", 8.0))
    require_min_points = int((cfg.get("calibration") or {}).get("require_min_points", 6))

    return {
        "cfg": cfg,
        "cfg_dir": cfg_dir,
        "layout_path": layout_path,
        "layout": layout,
        "cameras": cameras,
        "calib_path": calib_path,
        "intrinsics_path": intrinsics_path,
        "maro_api_base": api_base,
        "maro_cache_dir": cache_dir,
        "save_residual_px_max": save_residual_px_max,
        "require_min_points": require_min_points,
    }


app = FastAPI(title="Tracking System — Multi-Camera Calibration")

_context: dict[str, Any] = {}
_pool: Optional[CameraSnapshotPool] = None
_maro_assets: Any = None
_led: Optional[LedMarker] = None
_auto_cal_lock = threading.Lock()
_auto_cal_state: dict[str, Any] = {
    "running": False,
    "results": {},
    "error": None,
}


@app.on_event("startup")
def _startup() -> None:
    global _pool, _maro_assets, _led
    ctx = _load_runtime_config()
    _context.update(ctx)
    undistort = CameraUndistortRegistry.from_path(Path(ctx["intrinsics_path"]))
    _pool = CameraSnapshotPool(ctx["cameras"], undistort=undistort)
    try:
        _maro_assets = load_maro_floorplan(
            ctx["maro_api_base"],
            Path(ctx["maro_cache_dir"]),
        )
        _context["maro_assets"] = _maro_assets
    except Exception as exc:
        logger.error("calibrate_web: failed to load Maro floor plan: %s", exc)
        raise
    # LED marker — optional, only enabled if cache has artnet_mapping.json
    try:
        _led = LedMarker.from_cache(Path(ctx["maro_cache_dir"]))
        if _led.strip_names():
            logger.info("led_marker: %d strips ready (%s)",
                        len(_led.strip_names()), ", ".join(_led.strip_names()))
        else:
            _led = None
            logger.info("led_marker: no ARGB strips with mapping found — LED mode disabled")
    except Exception as exc:
        logger.warning("led_marker: failed to init (%s) — LED mode disabled", exc)
        _led = None
    logger.info(
        "calibrate_web up: cameras=%d layout=%s calib=%s maro=%s (%dx%d)",
        len(ctx["cameras"]),
        ctx["layout_path"].name,
        ctx["calib_path"],
        ctx["maro_api_base"],
        _maro_assets.plan_width,
        _maro_assets.plan_height,
    )


@app.on_event("shutdown")
def _shutdown() -> None:
    global _pool, _led
    if _led is not None:
        try:
            _led.all_off()
        except Exception as exc:
            logger.warning("led_marker: shutdown all_off failed: %s", exc)
    if _pool is not None:
        _pool.release_all()
        _pool = None


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if not html_path.is_file():
        raise HTTPException(status_code=500, detail="index.html missing")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/cameras")
def api_cameras() -> JSONResponse:
    """Active camera list + current calibration status for each."""

    calib_data = read_calibrations(_context["calib_path"])
    rows: list[dict[str, Any]] = []
    for cam in _context["cameras"]:
        name = cam["name"]
        entry = calib_data.get(name) or {}
        mode = str(entry.get("mode") or "missing")
        rows.append(
            {
                "name": name,
                "room": cam.get("room", ""),
                "rotate": int(cam.get("rotate") or 0),
                "calib_status": mode if entry else "missing",
                "calibrated_at": entry.get("calibrated_at"),
                "calib_image_width": entry.get("calib_image_width"),
                "calib_image_height": entry.get("calib_image_height"),
            }
        )
    return JSONResponse({"cameras": rows})


@app.get("/api/maro/floorplan/meta")
def api_maro_floorplan_meta() -> JSONResponse:
    assets = _context.get("maro_assets")
    if assets is None:
        raise HTTPException(status_code=503, detail="Maro floor plan not loaded")
    return JSONResponse(assets.meta_dict())


@app.get("/api/maro/floorplan/bg")
def api_maro_floorplan_bg() -> Response:
    assets = _context.get("maro_assets")
    if assets is None:
        raise HTTPException(status_code=503, detail="Maro floor plan not loaded")
    return Response(
        content=assets.bg_png,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/maro/floorplan/overlay.json")
def api_maro_floorplan_overlay() -> JSONResponse:
    assets = _context.get("maro_assets")
    if assets is None:
        raise HTTPException(status_code=503, detail="Maro floor plan not loaded")
    return JSONResponse(assets.overlay_for_ui())


@app.get("/api/floor_plan.png")
def api_floor_plan_legacy() -> Response:
    return api_maro_floorplan_bg()


@app.get("/api/floor_plan_meta")
def api_floor_plan_meta_legacy() -> JSONResponse:
    return api_maro_floorplan_meta()


@app.post("/api/snapshot_all")
def api_snapshot_all() -> JSONResponse:
    """Refresh all snapshots, return per-camera tokens for ``/api/snapshot/{cam}.jpg``."""

    assert _pool is not None
    results = _pool.refresh_all()
    ts = time.time()
    out: dict[str, Any] = {}
    for cam_name, info in results.items():
        out[cam_name] = {
            "ok": info["ok"],
            "ts": ts,
            "w": info.get("w"),
            "h": info.get("h"),
            "url": f"/api/snapshot/{cam_name}.jpg?ts={ts:.3f}",
            "error": info.get("error"),
        }
    return JSONResponse({"snapshots": out, "ts": ts})


@app.get("/api/snapshot/{cam_name}.jpg")
def api_snapshot(cam_name: str, ts: Optional[float] = Query(default=None)) -> Response:
    assert _pool is not None
    jpeg = _pool.get_latest_jpeg(cam_name)
    if jpeg is None:
        raise HTTPException(status_code=503, detail=f"no frame yet for {cam_name!r}")
    headers = {"Cache-Control": "no-store"}
    return Response(content=jpeg, media_type="image/jpeg", headers=headers)


@app.get("/api/led/strips")
def api_led_strips() -> JSONResponse:
    """List ARGB strips available for marker calibration + their geometry."""
    if _led is None:
        return JSONResponse({"available": False, "strips": []})
    assets = _context.get("maro_assets")
    out = []
    for name in _led.strip_names():
        info = _led.info(name)
        if not info:
            continue
        # Pre-compute plan_px positions for every marker so the UI can show them all
        markers_px = []
        for idx in range(info["num_markers"]):
            mm = _led.marker_position_mm(name, idx)
            if mm is None or assets is None:
                continue
            px = assets.mm_to_plan_px(mm[0], mm[1])
            markers_px.append({
                "idx": idx,
                "mm": [round(mm[0], 1), round(mm[1], 1)],
                "plan_px": [round(px[0], 1), round(px[1], 1)],
            })
        out.append({**info, "markers": markers_px})
    return JSONResponse({"available": True, "strips": out})


@app.post("/api/led/marker")
def api_led_marker(payload: dict[str, Any]) -> JSONResponse:
    """Light a single marker (one logical pixel = 6 LEDs) on the selected strip.
    Body: {strip, idx, rgbw?=[r,g,b,w] (0-255)}"""
    if _led is None:
        raise HTTPException(status_code=503, detail="LED marker mode unavailable (no mapping)")
    strip = str(payload.get("strip", "")).strip()
    idx = int(payload.get("idx", 0))
    raw = payload.get("rgbw") or [0, 0, 0, 255]
    rgbw = tuple(max(0, min(255, int(v))) for v in raw[:4])
    while len(rgbw) < 4:
        rgbw = (*rgbw, 0)
    result = _led.light_marker(strip, idx, rgbw)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "light failed"))
    mm = _led.marker_position_mm(strip, idx)
    assets = _context.get("maro_assets")
    plan_px = assets.mm_to_plan_px(mm[0], mm[1]) if (mm and assets) else None
    return JSONResponse({
        **result,
        "mm": [round(mm[0], 1), round(mm[1], 1)] if mm else None,
        "plan_px": [round(plan_px[0], 1), round(plan_px[1], 1)] if plan_px else None,
    })


@app.post("/api/led/off")
def api_led_off(payload: dict[str, Any] = None) -> JSONResponse:
    """Turn off LEDs. Body: {strip?} (omit to clear all strips)."""
    if _led is None:
        return JSONResponse({"ok": True, "cleared": []})
    strip = (payload or {}).get("strip")
    return JSONResponse(_led.all_off(strip))


@app.post("/api/led/all_markers")
def api_led_all_markers(payload: dict[str, Any] = None) -> JSONResponse:
    """Light a block pattern on all strips simultaneously.
    Body: {rgbw?, on_markers?=5, off_markers?=5}
    Default = 50 cm lit / 50 cm dark (1 marker = 10 cm = 6 LEDs)."""
    if _led is None:
        raise HTTPException(status_code=503, detail="LED unavailable")
    body = payload or {}
    raw = body.get("rgbw") or [255, 255, 255, 255]
    rgbw = tuple(max(0, min(255, int(v))) for v in raw[:4])
    while len(rgbw) < 4:
        rgbw = (*rgbw, 0)
    on_n = int(body.get("on_markers", 5))
    off_n = int(body.get("off_markers", 5))
    result = _led.light_blocks(on_markers=on_n, off_markers=off_n, rgbw=rgbw)
    # Augment with plan-pixel positions
    assets = _context.get("maro_assets")
    lit_with_px: dict[str, list[dict[str, Any]]] = {}
    for name, indices in result.get("lit_per_strip", {}).items():
        positions = []
        for idx in indices:
            mm = _led.marker_position_mm(name, idx)
            if mm is None or assets is None:
                continue
            px = assets.mm_to_plan_px(mm[0], mm[1])
            positions.append({
                "idx": idx,
                "plan_px": [round(px[0], 1), round(px[1], 1)],
            })
        lit_with_px[name] = positions
    result["lit_with_px"] = lit_with_px
    return JSONResponse(result)


def _run_led_auto_cal(camera_names: list[str]) -> None:
    global _auto_cal_state
    ctx = _context
    cfg = ctx.get("cfg") or {}
    auto_raw = cfg.get("auto_calibration") or {}
    led_raw = auto_raw.get("led") or {}
    acfg = LedAutoCalConfig(
        marker_step=int(led_raw.get("marker_step", 5)),
        settle_ms=int(led_raw.get("settle_ms", 400)),
        min_markers=int(led_raw.get("min_markers", 6)),
        save_residual_px_max=float(led_raw.get("save_residual_px_max", 8.0)),
    )
    results: dict[str, Any] = {}
    try:
        if _led is None or _pool is None:
            raise RuntimeError("LED strips or camera pool not available")

        def grab(cam_id: str):
            return _pool.get_latest_oriented(cam_id)

        calibrator = LedAutoCalibrator(
            led=_led,
            floorplan=_maro_assets,
            cfg=acfg,
            grab_frame=grab,
        )
        new_entries: dict[str, dict[str, Any]] = {}
        for cam_id in camera_names:
            if cam_id not in _pool.names():
                results[cam_id] = {"ok": False, "error": "unknown camera"}
                continue
            _pool.refresh_all(settle_seconds=0.3)
            res = calibrator.calibrate_camera(cam_id)
            if not res.ok or res.fit is None:
                results[cam_id] = {
                    "ok": False,
                    "error": res.error,
                    "num_markers": res.num_markers,
                }
                continue
            frame = grab(cam_id)
            if frame is None:
                results[cam_id] = {"ok": False, "error": "no frame"}
                continue
            h, w = frame.shape[:2]
            new_entries[cam_id] = calibrator.build_calibration_entry(
                res, img_w=w, img_h=h
            )
            results[cam_id] = {
                "ok": True,
                "num_markers": res.num_markers,
                "residual_px_mean": res.residual_px_mean,
                "residual_px_max": res.residual_px_max,
            }
        if new_entries:
            assets = _maro_assets
            atomic_merge_calibrations(
                Path(ctx["calib_path"]),
                new_entries,
                document_meta={
                    "_coordinate_space": COORDINATE_SPACE,
                    "_plan_size_px": [
                        int(assets.plan_width),
                        int(assets.plan_height),
                    ],
                },
            )
    except Exception as exc:
        logger.exception("led auto-cal failed")
        with _auto_cal_lock:
            _auto_cal_state["error"] = str(exc)
    finally:
        with _auto_cal_lock:
            _auto_cal_state["running"] = False
            _auto_cal_state["results"] = results


@app.post("/api/auto-cal/led/start")
def api_auto_cal_led_start(payload: dict[str, Any] = None) -> JSONResponse:
    """Run LED sequence auto-calibration (background thread)."""

    if _led is None:
        raise HTTPException(status_code=503, detail="LED not configured")
    with _auto_cal_lock:
        if _auto_cal_state.get("running"):
            raise HTTPException(status_code=409, detail="auto-cal already running")
        _auto_cal_state["running"] = True
        _auto_cal_state["results"] = {}
        _auto_cal_state["error"] = None
    body = payload or {}
    raw_names = body.get("cameras")
    if raw_names:
        names = [str(n) for n in raw_names]
    else:
        names = list(_pool.names()) if _pool else []
    if not names:
        raise HTTPException(status_code=400, detail="no cameras to calibrate")
    threading.Thread(
        target=_run_led_auto_cal,
        args=(names,),
        name="led-auto-cal",
        daemon=True,
    ).start()
    return JSONResponse({"ok": True, "cameras": names})


@app.get("/api/auto-cal/led/status")
def api_auto_cal_led_status() -> JSONResponse:
    with _auto_cal_lock:
        return JSONResponse(dict(_auto_cal_state))


def _parse_positions(payload: dict[str, Any]) -> list[Position]:
    raw = payload.get("positions")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=400, detail="'positions' must be a non-empty array")
    cam_names = {c["name"] for c in _context["cameras"]}
    out: list[Position] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=f"position[{i}] must be an object")
        pid = str(row.get("id") or f"L{i + 1}")
        xy = row.get("world_xy_plan_px") or row.get("world_xy_mm") or row.get("world_mm")
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise HTTPException(
                status_code=400,
                detail=f"position[{i}].world_xy_plan_px must be [x_px, y_px]",
            )
        try:
            world = (float(xy[0]), float(xy[1]))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"position[{i}].world_xy_plan_px not numeric: {xy!r}",
            ) from exc
        clicks_raw = row.get("clicks") or {}
        if not isinstance(clicks_raw, dict):
            raise HTTPException(
                status_code=400,
                detail=f"position[{i}].clicks must be an object {{cam: [u,v]}}",
            )
        clicks: dict[str, tuple[float, float]] = {}
        for cam_name, uv in clicks_raw.items():
            if uv is None:
                continue
            if cam_name not in cam_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"position[{i}].clicks: unknown camera {cam_name!r}",
                )
            if not isinstance(uv, (list, tuple)) or len(uv) != 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"position[{i}].clicks[{cam_name}] must be [u, v]",
                )
            try:
                clicks[cam_name] = (float(uv[0]), float(uv[1]))
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"position[{i}].clicks[{cam_name}] not numeric: {uv!r}",
                ) from exc
        out.append(Position(id=pid, world_xy_plan_px=world, clicks=clicks))
    return out


def _plan_bounds_px() -> dict[str, float]:
    assets = _context.get("maro_assets")
    if assets is None:
        return {"x_min": 0.0, "y_min": 0.0, "x_max": 2700.0, "y_max": 1324.0}
    return {
        "x_min": 0.0,
        "y_min": 0.0,
        "x_max": float(assets.plan_width),
        "y_max": float(assets.plan_height),
    }


def _camera_image_size(cam_name: str) -> tuple[int, int]:
    assert _pool is not None
    size = _pool.last_size(cam_name)
    if size is not None:
        return size
    calib = read_calibrations(_context["calib_path"]).get(cam_name) or {}
    cw = calib.get("calib_image_width")
    ch = calib.get("calib_image_height")
    if cw and ch:
        return int(cw), int(ch)
    return (704, 576)


def _build_response(positions: list[Position]) -> dict[str, Any]:
    require_min = int(_context.get("require_min_points", 6))
    px_max = float(_context.get("save_residual_px_max", 8.0))
    cam_fits, position_stats = fit_all(positions)

    cams_out: dict[str, Any] = {}
    warnings: list[str] = []
    errors: list[str] = []
    for cam in _context["cameras"]:
        name = cam["name"]
        fit = cam_fits.get(name)
        if fit is None or fit.H is None:
            cams_out[name] = {
                "ok": False,
                "num_points": fit.num_points if fit else 0,
                "error": fit.error if fit else "no clicks for this camera",
            }
            continue
        if fit.num_points < require_min:
            warnings.append(
                f"{name}: only {fit.num_points} landmarks (need >= {require_min})"
            )
        if fit.num_points == 4:
            warnings.append(
                f"{name}: exactly 4 correspondences — residual is not a reliable quality check; "
                f"use >= {require_min}"
            )
        if fit.residual_px_mean > px_max:
            errors.append(
                f"{name}: mean residual {fit.residual_px_mean:.1f} px exceeds {px_max:.0f} px"
            )
        green = fit.num_points >= require_min and fit.residual_px_mean <= 5.0
        cams_out[name] = {
            "ok": True,
            "green": green,
            "num_points": fit.num_points,
            "residual_px_mean": round(fit.residual_px_mean, 2),
            "residual_px_max": round(fit.residual_px_max, 2),
            "worst_position_id": fit.worst_position_id,
            "per_position_residual_px": {
                k: round(v, 2) for k, v in fit.per_position_residual_px.items()
            },
            "used_positions": list(fit.used_position_ids),
            "H": fit.H.tolist(),
        }

    positions_out: list[dict[str, Any]] = []
    for pos in positions:
        stat = position_stats.get(pos.id)
        positions_out.append(
            {
                "id": pos.id,
                "world_xy_plan_px": [pos.world_xy_plan_px[0], pos.world_xy_plan_px[1]],
                "contributing_cams": list(stat.contributing_cams) if stat else [],
                "cross_camera_disagreement_px": (
                    round(stat.disagreement_px, 1)
                    if stat and stat.disagreement_px is not None
                    else None
                ),
            }
        )

    return {
        "coordinate_space": COORDINATE_SPACE,
        "cameras": cams_out,
        "positions": positions_out,
        "warnings": warnings,
        "errors": errors,
    }


@app.post("/api/compute")
def api_compute(payload: dict[str, Any]) -> JSONResponse:
    positions = _parse_positions(payload)
    body = _build_response(positions)
    return JSONResponse(body)


@app.post("/api/save")
def api_save(payload: dict[str, Any]) -> JSONResponse:
    positions = _parse_positions(payload)
    force = bool(payload.get("force", False))
    body = _build_response(positions)

    if body["errors"] and not force:
        return JSONResponse(
            {
                "status": "rejected",
                "reason": "validation_failed",
                **body,
            },
            status_code=400,
        )

    new_entries: dict[str, dict[str, Any]] = {}
    cam_fits, _ = fit_all(positions)
    assets = _context.get("maro_assets")
    plan_bounds = _plan_bounds_px()
    calibrated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    for cam in _context["cameras"]:
        name = cam["name"]
        fit = cam_fits.get(name)
        if fit is None or fit.H is None:
            continue
        img_w, img_h = _camera_image_size(name)
        new_entries[name] = build_camera_entry(
            fit=fit,
            img_w=img_w,
            img_h=img_h,
            plan_bounds_px=plan_bounds,
            calibrated_at=calibrated_at,
        )

    if not new_entries:
        raise HTTPException(
            status_code=400,
            detail="no camera produced a valid fit; nothing written",
        )

    doc_meta = {
        "_coordinate_space": COORDINATE_SPACE,
        "_plan_size_px": [
            int(assets.plan_width) if assets else 2700,
            int(assets.plan_height) if assets else 1324,
        ],
        "_maro_api_base": str(_context.get("maro_api_base", "")),
    }
    atomic_merge_calibrations(
        _context["calib_path"], new_entries, document_meta=doc_meta
    )
    body["status"] = "ok"
    body["written"] = sorted(new_entries.keys())
    body["calib_path"] = str(_context["calib_path"])
    return JSONResponse(body)


@app.get("/api/session/sample")
def api_session_sample() -> JSONResponse:
    """Empty session template the frontend can use as a starting point."""

    cams = [c["name"] for c in _context["cameras"]]
    assets = _context.get("maro_assets")
    meta = assets.meta_dict() if assets else {}
    return JSONResponse(
        {
            "version": 2,
            "coordinate_space": COORDINATE_SPACE,
            "plan_meta": meta,
            "cameras": cams,
            "positions": [],
        }
    )
