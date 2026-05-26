# Multi-camera calibration web tool

Browser-based homography calibration. Stand at a position, click it on
the floor plan, click your feet in every camera tile that sees you.
Shared world points pin all cameras to the same coordinate system —
unlike [`tools/calibrate_homography.py`](../../tools/calibrate_homography.py),
which fits each camera in isolation.

## Run

Install once (already in
[`tracking_engine/requirements.txt`](../requirements.txt)):

```powershell
python -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.27" "python-multipart>=0.0.9"
```

### On-site (real cameras)

From the repo root:

```bash
python -m tracking_engine.calibrate_web
# or, equivalent:
python -m uvicorn tracking_engine.calibrate_web.app:app --host 0.0.0.0 --port 8090
```

Open `http://<host>:8090` on any device on the LAN. Reads cameras from
the same `TRACKING_CONFIG` YAML (default
`tracking_engine/config.multi_camera.yaml`) the runtime uses; writes
`tracking_engine/calibration/camera_calibrations.json` atomically.

### Off-site dry run (no LAN, no Pi)

Use the saved stills from `stills/` to exercise the full UI — click
flow, recompute, save — without being on the camera network. Each
`--video NAME=PATH` flag substitutes one camera's RTSP feed with an
image (or a video file, same flag shape as
`python -m tracking_engine.multi_camera`):

```powershell
python -m tracking_engine.calibrate_web `
    --video cam_kwz_sw=stills/cam_kwz_sw.png `
    --video cam_kwz_nw=stills/cam_kwz_nw.png `
    --video cam_kwz_ne=stills/cam_kwz_ne.png `
    --video cam_kwz_se=stills/cam_kwz_se.png `
    --video cam_yoga_ne=stills/cam_yoga_ne.png `
    --video cam_yoga_se=stills/cam_yoga_se.png `
    --video cam_hallway_n=stills/cam_hallway_n.png
```

PNG/JPG paths are detected by extension and re-served as a static
frame; .mp4/.mkv loop on EOF. The stills produced by
`tools/probe_rtsp.py --save-stills` are already pre-rotated to match
the runtime orientation (per
[`config.multi_camera.yaml`](../config.multi_camera.yaml) `rotate:`
fields), so the snapshot pool **does not re-apply rotation to static
image overrides**. Live RTSP and video-file overrides still get the
configured `rotate:` so they match the runtime exactly.

If you ever hand the tool a raw, un-rotated PNG (for example one
straight from `ffmpeg`'s `image2` muxer on an upside-down stream),
re-orient it offline first — the per-camera `rotate:` won't be applied.

**Off-site is a UI / flow / math dry run only**: the saved stills show
empty rooms, not the operator at known positions, so any calibration
you save from a stills session is not real. Use this mode to verify
clicks, sliders, residual badges, save round-trip; do the actual
calibration on site with live cameras.

A systemd unit ships at
[`deploy/systemd/tracking-calibrate-web.service`](../../deploy/systemd/tracking-calibrate-web.service) —
install but keep it disabled by default; start only during calibration
sessions (RTSP grab cost on the Pi is non-zero).

## How it differs from the legacy tool

| | `tools/calibrate_homography.py` | `tracking_engine.calibrate_web` |
|---|---|---|
| Cameras per session | one | all enabled streams at once |
| World coords | type mm by hand | click on floor plan (auto mm) |
| Cross-camera consistency | **not enforced** | guaranteed by shared `P_k` |
| Validator | per-camera reprojection residual only | + per-position cross-camera disagreement |
| Where it runs | OpenCV window, laptop only | browser, Pi or laptop |
| Output | identical JSON schema | identical JSON schema |

Both write `mode: "homography"` entries with the same fields
(`H`, `floor_bounds_mm`, `calib_image_width/height`, `image_points`,
`world_points_mm`, `calibrated_at`, `_comment`) so the runtime in
[`tracking_engine/pipeline/homography.py`](../pipeline/homography.py)
loads either source unchanged.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/` | static UI |
| GET    | `/api/cameras` | active cameras + current calib status |
| GET    | `/api/floor_plan.png` | floor plan image |
| GET    | `/api/floor_plan_meta` | envelope mm + image size |
| POST   | `/api/snapshot_all` | refresh every RTSP, return tile URLs |
| GET    | `/api/snapshot/{cam}.jpg` | latest oriented JPEG for one camera |
| POST   | `/api/compute` | preview: per-cam fit + cross-cam disagreement |
| POST   | `/api/save` | recompute and atomically merge into the JSON (pass `force: true` to bypass the 250 mm error threshold) |

## Validation

`/api/save` enforces:

- Each camera must have ≥ 4 clicked positions (else no fit, that camera
  is left untouched in the JSON).
- Warnings if any camera has < 6 positions (a 4-point fit is
  mathematically exact and tells you nothing).
- **Rejects** save if any camera's mean residual > 250 mm, unless the
  request body has `"force": true`.

The frontend recomputes automatically ~350 ms after every click and
shows the live residual + disagreement so you can see whether to keep
clicking before you save.
