# Multi-camera tracking engine

Run several placements-defined streams through **one shared person detector**, **one ByteTrack instance per camera**, floor-plane projection via [`calibration/camera_calibrations.json`](calibration/camera_calibrations.json), then **one HTTP POST per camera per tick** (same JSON schema as single-camera mode).

Authoritative camera names and room assignments come from the exported placement file [`camera_placement_plan/output/cameras_config.json`](../camera_placement_plan/output/cameras_config.json).

## Behaviour summary

| Topic | Detail |
|--------|--------|
| Layout source | `multi_camera.cameras_layout_file` in YAML → JSON `cameras[].name`, `room`, etc. |
| Streams | List under `multi_camera.streams`: each row defines `name` (must match layout), `rtsp_url`, `enabled`. |
| Calibration keys | Each enabled stream name must exist as a key in `camera_calibrations.json`. |
| Zones | Each POST payload uses `zone = layout room` string (`resolve_active_streams` sets this from JSON `room`). |
| Tracker IDs | `persons[].id` values (`t<number>`) are **local to that camera’s ByteTrack** — `t3` on camera A is unrelated to `t3` on camera B. |
| Cross-camera identity | Not fused yet; downstream systems should merge world coordinates if global IDs are required. |
| RTSP latency | With `use_latest_frame_rtsp: true`, each RTSP feed runs a background grab thread that keeps only the **latest** decoded frame so one slow inference loop does not backlog stale frames. |
| Optional POST latency | With `poster.include_latency_in_post: true`, each POST includes `latency_ms` for **grab/detect/track/geom sums across cameras in that tick** (HTTP time is excluded so values stay identical on every POST from the same tick). |

## Configure

Use [`config.multi_camera.yaml`](config.multi_camera.yaml):

1. Point `multi_camera.cameras_layout_file` at `camera_placement_plan/output/cameras_config.json` (repo ships a relative path from `tracking_engine/`).
2. Under `multi_camera.streams`, set real RTSP URLs and `enabled: true` for each camera you want to run.
3. Ensure [`calibration/camera_calibrations.json`](calibration/camera_calibrations.json) contains matching keys (`cam_kwz_sw`, … `cam_hallway_n`). Dummy bounds use room polygons from the placement JSON; replace with **`homography`** per camera when calibrated.

### Calibration

Two tools write the same JSON schema — pick whichever fits the situation:

- **Recommended: browser-based multi-camera tool** ([`tracking_engine/calibrate_web`](calibrate_web/__init__.py)). Stand at a position, click it once on the floor plan, then click your feet in every camera tile that sees you. Shared world points pin all cameras into the same coordinate system, so hand-off in overlap zones (4 cams in K/WZ, 2 in Yoga) stops jumping. Live per-position cross-camera disagreement readout flags miss-clicks.

  ```bash
  uvicorn tracking_engine.calibrate_web.app:app --host 0.0.0.0 --port 8090
  ```

  Open `http://<pi-or-laptop>:8090` from any device on the LAN. ≥ 6 positions per camera required before save; mean residual > 250 mm is rejected unless you tick **force**. Writes atomically to `tracking_engine/calibration/camera_calibrations.json`.

  Hand the homeowner [`docs/calibration_day_handover.md`](../docs/calibration_day_handover.md) — they can complete the whole walk-through themselves, phone in hand, without a technician on site.

- **Fallback for one camera at a time:** [`tools/calibrate_homography.py`](../tools/calibrate_homography.py). Useful if only a single camera moved and you don't want to re-do a full session — points are typed in mm by hand, so the per-camera fit will not auto-agree with the others in overlap zones.

## Run

From repository root (`Tracking_System/`):

```bash
python -m tracking_engine.multi_camera --config tracking_engine/config.multi_camera.yaml
```

### Laptop replay without RTSP

Use one clip per camera name:

```bash
python -m tracking_engine.multi_camera --config tracking_engine/config.multi_camera.yaml \
  --video cam_kwz_sw=path/to/a.mp4 \
  --video cam_kwz_nw=path/to/b.mp4
```

`--video NAME=PATH` may be repeated; it overrides `rtsp_url` for that layout name only.  
Each NAME must also have **`enabled: true`** under `multi_camera.streams` (disabled streams are skipped, so extra `--video` flags error).

### Preview window

Set `runtime.show_preview: true` and `runtime.show_preview_camera: <name>` to visualize one enabled stream (press `q` to quit).

### Limit runtime

`runtime.max_ticks` counts **full sweeps** over all enabled cameras (not per-camera frames).

## Logging

Stderr lines look like:

```text
[multi] tick=30  grab=... detect=... track=... geom=... post=... frame_total=...
```

Stages are **sums over cameras that produced a frame that tick**. If no RTSP frame is ready yet, the tick may carry zeros and the loop sleeps briefly (`~20 ms`) to avoid busy-waiting.

## Operational notes

- **Threads**: RTSP latest-frame readers are daemon threads; always stop the process cleanly (`Ctrl+C`) so OpenCV windows close predictably.
- **Detector**: One shared model instance serializes inference across cameras in the main thread — simple and thread-safe for Ultralytics CPU / Hailo wiring as implemented today.
- **Failures**: If any POST fails while persons were present, stderr prints a throttled error similar to single-camera mode.

## Relation to single-camera entrypoint

[`python -m tracking_engine`](README.md) (`main.py`) remains the minimal single-stream MVP. Multi-camera adds [`multi_camera.py`](multi_camera.py) only; mock sink and poster contract stay the same.
