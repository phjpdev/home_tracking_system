# Bird's Eye View (BEV) — Implementation Roadmap

Multi-camera tracking today: detect per camera → homography foot point → fuse dots on the Maro plan.

**BEV target:** warp each camera onto the floor plane, stitch into **one top-down image per zone**, detect and track **once** on that image, then map positions to Maro plan coordinates.

```text
LEGACY (current)                    BEV (target)
────────────────                    ────────────
N cameras × detect → fuse dots      per zone: N cameras → 1 stitched image → 1× detect → dots
```

---

## End state

| Zone | Cameras | Stitch | Detector runs |
|------|---------|--------|---------------|
| K/WZ | cam_kwz_sw, nw, ne, se | 4 → 1 BEV frame | 1 |
| Hallway | cam_hallway_n | 1 → 1 (warp only) | 1 |
| Yoga | cam_yoga_ne, cam_yoga_se | 2 → 1 BEV frame | 1 |

Output: POST to Maro `/tracking/positions` (same schema as today). Optional: live BEV preview per zone in Maro or a debug tool.

---

## Reusable components

| Existing | BEV use |
|----------|---------|
| `camera_calibrations.json`, `calibrate_web` | Homography `H` (image → plan px) |
| Maro floor plan + zone polygons | BEV canvas ROI per zone |
| YOLO + ByteTrack | Run on stitched BEV frame |
| `PositionPoster` | Unchanged |
| `plan_fusion` | Simpler or optional (single image → fewer duplicates) |
| Thermal / MQTT presence | Optional gate (see Phase 10) |

---

## Architecture

```text
┌─────────┐ ┌─────────┐ ┌─────────┐
│ cam A   │ │ cam B   │ │ cam C   │   (per zone)
└────┬────┘ └────┬────┘ └────┬────┘
     │ warp H    │ warp H    │ warp H
     └───────────┼───────────┘
                 ▼
          ┌──────────────┐
          │ stitch/blend │  → BEV frame (zone ROI)
          └──────┬───────┘
                 ▼
          ┌──────────────┐
          │ YOLO person  │
          │ ByteTrack    │
          └──────┬───────┘
                 ▼
          BEV px → Maro plan px → POST
```

Homography maps image pixels to Maro plan pixels (`foot_point_to_plan_px` in `pipeline/homography.py`). BEV warp uses the same `H` with `cv2.warpPerspective`, scaled for live frame size vs `calib_image_width/height`.

---

## Phase 0 — Layout freeze

**Goal:** Stable camera positions and room layout before calibration or BEV work.

**Tasks**
- Document final camera mounts (height, tilt, rotation overrides in config).
- Confirm RTSP stream (sub 704×576 vs main).
- Note furniture layout per zone.

**Deliverable:** Frozen `multi_camera.streams` and site photos per camera.

**Exit criteria:** Cameras and furniture unchanged for the calibration + BEV build window.

---

## Phase 1 — Coverage audit

**Goal:** Every walkable floor corner visible; enough overlap to stitch.

**Tasks**
- Per zone, map which cameras cover it (from `cameras_config.json`).
- Mark blind spots on the Maro plan.
- Adjust camera aim until each zone has full floor coverage and ≥30% overlap between adjacent views.

**Tool (build in this phase)**
- `tools/bev_coverage.py` — draw each camera's warped footprint on the plan using existing `H`.

**Exit criteria**
- K/WZ: all four floor corners seen by at least one camera.
- Hallway: full corridor floor visible.
- Yoga: full floor visible from two cameras with overlap.

---

## Phase 2 — Zone BEV canvas

**Goal:** Define pixel canvas per zone on the Maro plan.

**Tasks**
- Load zone polygons from Maro overlay (`pipeline/maro_floorplan.py`, `zones_from_overlay`).
- Per zone: bounding box or tight polygon mask in plan px.
- Choose output resolution:
  - **Full plan px** in ROI — highest quality, heavy on Pi.
  - **Downscaled** (e.g. 640×480 per zone) — recommended for YOLO on Pi 5.

**Config example** (new section or `config.bev.yaml`):

```yaml
bev:
  zones:
    kwz:
      cameras: [cam_kwz_sw, cam_kwz_nw, cam_kwz_ne, cam_kwz_se]
      roi_plan_px: {x_min: 80, y_min: 500, x_max: 700, y_max: 1150}
      output_size: [640, 480]
    hallway:
      cameras: [cam_hallway_n]
      roi_plan_px: { ... }
      output_size: [320, 640]
    yoga:
      cameras: [cam_yoga_ne, cam_yoga_se]
      roi_plan_px: { ... }
      output_size: [480, 360]
```

**Deliverable:** ROI validated on Maro plan background image.

**Exit criteria:** ROI matches physical room bounds; no critical floor area clipped.

---

## Phase 3 — Single-camera warp (POC)

**Goal:** One camera → one top-down image aligned with the floor.

**Tasks**
- Add `pipeline/bev_warp.py`:
  - Input: BGR frame, `Calibration`, zone ROI, output size.
  - Scale `H` for live frame dimensions (reuse `_scale_uv` logic from `homography.py`).
  - `cv2.warpPerspective` into zone canvas; optional alpha mask for invalid regions.
- Add `tools/bev_preview.py`:
  - `python -m tracking_engine.tools.bev_preview --camera cam_kwz_sw --zone kwz --out bev_sw.jpg`
  - Optional: live RTSP preview.

**Deliverable:** Static or short video: warped view with floor tiles/rug appearing top-down.

**Exit criteria:** Landmark residuals < 10 px; floor lines visually aligned with plan.

**Depends on:** Phase 0–1 calibration (after any camera moves).

---

## Phase 4 — Multi-camera stitch (one zone)

**Goal:** All cameras in a zone → one composite BEV frame.

**Tasks**
- Add `pipeline/bev_stitch.py`:
  - Warp each feed to the same zone canvas.
  - Blend strategy (iterate in order):
    1. Per-pixel max (fast, visible seams).
    2. Average in overlap regions.
    3. Distance feather from seam / camera center (best quality).
  - Optional: per-camera exposure normalization before blend.
- Add `tools/bev_live_view.py --zone kwz` (2–5 FPS on Pi or dev machine).

**Deliverable:** Live or recorded K/WZ stitched BEV.

**Exit criteria**
- One person appears as one blob in BEV (not four copies).
- No large black holes in walkable area.
- Seam mismatch < ~20 px on floor features (tiles, rug).

**Depends on:** Phase 3; Phase 5 calibration quality strongly affects seams.

---

## Phase 5 — Calibration for BEV

**Goal:** Homography accurate enough for **image** stitching, not only point projection.

**Tasks**
- Recalibrate all cameras via `calibrate_web` after final positions.
- Requirements per camera:
  - 8–10 landmarks, spread in 2D on the floor (not collinear).
  - Include ROI corners and zone boundaries.
  - mean residual < 5 px, max < 15 px.
- Add collinearity guard in calibrate-web (reject / warn on near-straight point sets).

**Exit criteria:** Stitched seams align on visible floor features across overlapping cameras.

---

## Phase 6 — Detection on BEV

**Goal:** YOLO person detection on stitched frame only.

**Tasks**
- Add `bev/run.py` or `pipeline.mode: bev` in config.
- Per zone per tick: grab frames → stitch → `detector.detect(bev_frame)` with `class_id: 0`.
- Map detection foot point: BEV coords → full Maro plan px:

```text
plan_x = roi.x_min + bev_x * (roi_width / bev_width)
plan_y = roi.y_min + bev_y * (roi_height / bev_height)
```

- POST via existing `PositionPoster`.

**Config sketch**

```yaml
pipeline:
  mode: bev   # legacy | bev

bev:
  enabled: true
  zones: [kwz]   # extend to hallway, yoga

detector:
  class_id: 0
  conf: 0.35
```

**Deliverable:** K/WZ BEV pipeline posting person dots to Maro.

**Exit criteria:** Two people in K/WZ → two plan dots; no per-camera duplicate dots.

---

## Phase 7 — Tracking on BEV

**Goal:** Stable track IDs on the BEV canvas.

**Tasks**
- One `ByteTracker` per zone on BEV detections.
- Remap tracked bbox bottom-center to plan px before POST.
- Optional light EMA in plan space (simpler than multi-camera `plan_fusion`).

**Exit criteria:** Track IDs stable across frames; minimal ID swap when paths cross.

---

## Phase 8 — All zones

**Goal:** Full-house BEV pipeline.

**Tasks**
- Hallway: single-camera warp (no stitch).
- Yoga: two-camera stitch.
- Merge zone outputs: one POST per tick with `cam_id: fused` or per-zone `cam_id: bev_kwz`, etc.
- Maro: consume same `/tracking/positions` schema.

**Exit criteria:** Persons in any zone appear as correct dots on the full Maro plan.

---

## Phase 9 — Performance (Pi 5)

**Goal:** Acceptable latency for live presence.

**Baseline (legacy):** ~5 s/tick, 7× CPU YOLO on 704×576 streams.

**BEV target:** 3× YOLO (one per zone) on downscaled BEV frames.

**Tasks**
- Downscale BEV `output_size` per zone.
- Parallel warp on thread pool (warp is cheap vs detect).
- Hailo on BEV tensor if model input size fits; else `yolov8n` CPU.
- Prometheus / log: `bev_stitch_ms`, `bev_detect_ms`, `bev_post_ms`.

**Exit criteria:** Full-house cycle < 3 s on Pi 5 CPU; < 1.5 s stretch goal with Hailo.

---

## Phase 10 — Radar / thermal fusion

**Goal:** Combine BEV optical tracks with non-camera sensors.

**Tasks**
- Define radar output: room presence only vs plan coordinates.
- Presence-only: reuse `thermal_gate` pattern — suppress optical dots near SZ/BZ when MQTT says occupied.
- Position radar: cluster radar + BEV dots in plan px (`plan_fusion.cluster_distance_px`).
- Document room mode: optical only, radar only, fused.

**Exit criteria:** No conflicting dots in privacy zones; single presence truth per room where required.

---

## Phase 11 — Production hardening

**Tasks**
- Keep `multi_camera.py` as `pipeline.mode: legacy` until BEV is default.
- Unit tests: `bev_warp`, `bev_coords` (BEV ↔ plan), stitch with synthetic frames.
- Reload calibrations without full service restart.
- Metric: BEV valid pixel coverage % per zone (detect black/unwarped holes).
- Runbook section: recalibrate after camera bump; link from `production_camera_only_runbook.md`.

---

## Suggested module layout

```text
tracking_engine/
  pipeline/
    bev_warp.py       # single frame → zone canvas
    bev_stitch.py     # N warped frames → one BEV image
    bev_coords.py     # BEV px ↔ Maro plan px
  bev/
    run.py            # main loop (grab → stitch → detect → track → post)
  tools/
    bev_preview.py    # single-cam warp debug
    bev_live_view.py  # live stitched preview
    bev_coverage.py   # camera footprints on plan
```

---

## Build order

| Step | Phase | Effort (est.) | Depends on |
|------|-------|---------------|------------|
| 1 | 0 Layout freeze | site | — |
| 2 | 1 Coverage audit | 1–2 d | 0 |
| 3 | 2 Zone ROI config | 0.5 d | 1 |
| 4 | 3 Single-cam warp | 2–3 d | 2, 5 |
| 5 | 5 Recalibrate | 1–2 d | 0 |
| 6 | 4 Multi-cam stitch (K/WZ) | 3–5 d | 3, 5 |
| 7 | 6 Detect on BEV | 2–3 d | 4 |
| 8 | 7 ByteTrack on BEV | 1–2 d | 6 |
| 9 | 8 All zones | 2–3 d | 7 |
| 10 | 9 Performance | 3–5 d | 8 |
| 11 | 10 Radar fusion | TBD | 8 |
| 12 | 11 Hardening | ongoing | 8 |

**First code milestone after layout freeze:** Phase 3 (`bev_preview` for one camera).

---

## Known limitations

- Homography assumes **floor plane (z = 0)**. People on stairs, seated high, or leaning on counters project incorrectly (same as legacy pipeline).
- BEV quality degrades at stitch seams if calibration drifts or cameras move.
- Single BEV frame per zone does not remove occlusion — a person hidden from all cameras in that zone will not appear.

---

## Related docs

- `docs/calibration_maro_plan_px.md` — plan-pixel homography workflow
- `docs/production_camera_only_runbook.md` — deploy and systemd
- `tracking_engine/config.multi_camera.yaml` — cameras, legacy fusion, detector
