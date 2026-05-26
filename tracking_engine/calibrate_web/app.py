"""FastAPI app: multi-camera homography calibration UI.

See :mod:`tracking_engine.calibrate_web` for the workflow rationale.
"""

from __future__ import annotations

import json
import logging
import os
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
from .calibration_io import atomic_merge_calibrations, read_calibrations
from .homography_fit import Position, fit_all
from .snapshot import CameraSnapshotPool

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

    coord = layout.get("coordinate_system") or {}
    envelope = coord.get("envelope_mm") or [19800, 10200]
    floor_plan_path = (
        layout_path.parent.parent / "floor_plan.png"
    ).resolve()

    return {
        "cfg": cfg,
        "cfg_dir": cfg_dir,
        "layout_path": layout_path,
        "layout": layout,
        "cameras": cameras,
        "calib_path": calib_path,
        "envelope_mm": [float(envelope[0]), float(envelope[1])],
        "floor_plan_path": floor_plan_path,
    }


app = FastAPI(title="Tracking System — Multi-Camera Calibration")

_context: dict[str, Any] = {}
_pool: Optional[CameraSnapshotPool] = None


@app.on_event("startup")
def _startup() -> None:
    global _pool
    ctx = _load_runtime_config()
    _context.update(ctx)
    _pool = CameraSnapshotPool(ctx["cameras"])
    logger.info(
        "calibrate_web up: cameras=%d layout=%s calib=%s",
        len(ctx["cameras"]),
        ctx["layout_path"].name,
        ctx["calib_path"],
    )


@app.on_event("shutdown")
def _shutdown() -> None:
    global _pool
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


@app.get("/api/floor_plan.png")
def api_floor_plan() -> FileResponse:
    fp: Path = _context["floor_plan_path"]
    if not fp.is_file():
        raise HTTPException(status_code=404, detail=f"floor_plan.png not found at {fp}")
    return FileResponse(str(fp), media_type="image/png")


@app.get("/api/floor_plan_meta")
def api_floor_plan_meta() -> JSONResponse:
    fp: Path = _context["floor_plan_path"]
    image_w = image_h = None
    if fp.is_file():
        try:
            import cv2

            img = cv2.imread(str(fp))
            if img is not None:
                image_h, image_w = int(img.shape[0]), int(img.shape[1])
        except Exception:  # pragma: no cover - defensive
            pass
    return JSONResponse(
        {
            "envelope_mm": _context["envelope_mm"],
            "image_w": image_w,
            "image_h": image_h,
        }
    )


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


def _parse_positions(payload: dict[str, Any]) -> list[Position]:
    raw = payload.get("positions")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=400, detail="'positions' must be a non-empty array")
    cam_names = {c["name"] for c in _context["cameras"]}
    out: list[Position] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=f"position[{i}] must be an object")
        pid = str(row.get("id") or f"P{i + 1}")
        xy = row.get("world_xy_mm") or row.get("world_mm")
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise HTTPException(
                status_code=400,
                detail=f"position[{i}].world_xy_mm must be [x_mm, y_mm]",
            )
        try:
            world = (float(xy[0]), float(xy[1]))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"position[{i}].world_xy_mm not numeric: {xy!r}",
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
        out.append(Position(id=pid, world_xy_mm=world, clicks=clicks))
    return out


def _floor_bounds_from_layout() -> dict[str, float]:
    env = _context.get("envelope_mm") or [19800.0, 10200.0]
    return {
        "x_min": 0.0,
        "x_max": float(env[0]),
        "y_min": 0.0,
        "y_max": float(env[1]),
    }


def _floor_bounds_for_camera(cam_name: str) -> dict[str, float]:
    layout = _context["layout"]
    cams = layout.get("cameras") or []
    row = next((c for c in cams if c.get("name") == cam_name), None)
    fallback = _floor_bounds_from_layout()
    if row is None:
        return fallback
    room = str(row.get("room") or "").strip()
    rooms = layout.get("rooms") or {}
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


def _build_response(
    positions: list[Position],
    require_min: int = 6,
) -> dict[str, Any]:
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
                f"{name}: only {fit.num_points} clicked positions (need >= {require_min})"
            )
        if fit.num_points == 4:
            warnings.append(
                f"{name}: exactly 4 correspondences — fit is mathematically exact (residual ~0); "
                "re-pick with >= 6 well-spread positions"
            )
        if fit.residual_mm_mean > 250.0:
            errors.append(
                f"{name}: mean residual {fit.residual_mm_mean:.0f} mm exceeds 250 mm threshold"
            )
        cams_out[name] = {
            "ok": True,
            "num_points": fit.num_points,
            "residual_mm_mean": round(fit.residual_mm_mean, 1),
            "residual_mm_max": round(fit.residual_mm_max, 1),
            "used_positions": list(fit.used_position_ids),
            "H": fit.H.tolist(),
        }

    positions_out: list[dict[str, Any]] = []
    for pos in positions:
        stat = position_stats.get(pos.id)
        positions_out.append(
            {
                "id": pos.id,
                "world_xy_mm": [pos.world_xy_mm[0], pos.world_xy_mm[1]],
                "contributing_cams": list(stat.contributing_cams) if stat else [],
                "cross_camera_disagreement_mm": (
                    round(stat.disagreement_mm, 1)
                    if stat and stat.disagreement_mm is not None
                    else None
                ),
            }
        )

    return {
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
    for cam in _context["cameras"]:
        name = cam["name"]
        fit = cam_fits.get(name)
        if fit is None or fit.H is None:
            continue
        img_w, img_h = _camera_image_size(name)
        image_points = [list(uv) for uv in fit.image_points]
        world_points = [list(xy) for xy in fit.world_points]
        new_entries[name] = {
            "mode": "homography",
            "floor_bounds_mm": _floor_bounds_for_camera(name),
            "H": [[float(v) for v in row] for row in fit.H.tolist()],
            "calib_image_width": int(img_w),
            "calib_image_height": int(img_h),
            "calibrated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "image_points": [[int(round(u)), int(round(v))] for u, v in image_points],
            "world_points_mm": [[float(x), float(y)] for x, y in world_points],
            "_comment": (
                "Generated by tracking_engine.calibrate_web (multi-camera shared-world "
                "calibration). Per-camera residual mean="
                f"{fit.residual_mm_mean:.1f}mm max={fit.residual_mm_max:.1f}mm "
                f"over {fit.num_points} shared positions."
            ),
        }

    if not new_entries:
        raise HTTPException(
            status_code=400,
            detail="no camera produced a valid fit; nothing written",
        )

    atomic_merge_calibrations(_context["calib_path"], new_entries)
    body["status"] = "ok"
    body["written"] = sorted(new_entries.keys())
    body["calib_path"] = str(_context["calib_path"])
    return JSONResponse(body)


@app.get("/api/session/sample")
def api_session_sample() -> JSONResponse:
    """Empty session template the frontend can use as a starting point."""

    cams = [c["name"] for c in _context["cameras"]]
    return JSONResponse(
        {
            "version": 1,
            "envelope_mm": _context["envelope_mm"],
            "cameras": cams,
            "positions": [],
        }
    )
