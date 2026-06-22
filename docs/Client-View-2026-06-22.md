# Home Tracking System — Latest Work Summary

This document summarizes recent progress on the Raspberry Pi tracking deployment: camera calibration, the multi-camera tracking engine, privacy-room (SZ/BZ) thermal tracking, and preparation for Bird's Eye View (BEV) camera placement.

---

## 1. Updated calibration

### What changed

- All seven PoE cameras were recalibrated in June 2026 using the browser-based **calibrate_web** tool (port 8090 on the Pi or laptop).
- Calibration uses **Point mode**: landmarks spread across the floor plan (not Line mode along a single corridor), which fixes degenerate homography that previously placed dots on a straight line.
- Results are stored in `tracking_engine/calibration/camera_calibrations.json` (homography matrix **H** per camera, ~5 px mean residual on site).
- Coordinates map camera pixels to the Maro floor plan (`maro_floorplan_px`) so person dots align with the dashboard.

### How to recalibrate (after any camera move)

1. Start: `python -m tracking_engine.calibrate_web` (on Pi LAN).
2. Open `http://<pi-ip>:8090`, click matching landmarks on plan + each camera tile.
3. Save when residuals are green; minimum 6 points per camera recommended.
4. Old calibration becomes invalid if cameras or furniture blocking the floor are moved.

---

## 2. Multi-camera tracking engine

### Current architecture

- Seven cameras on `192.168.178.70`–`76`, substream 704×576 H.264 for Pi CPU tracking.
- YOLO person detection (class 0) on each stream → foot point → homography → Maro plan.
- `plan_fusion` merges detections from overlapping cameras (especially 4× K/WZ).
- Positions POST to Maro at `http://127.0.0.1:8420/tracking/positions` on the Pi.
- Config: `tracking_engine/config.multi_camera.yaml`; deploy path `/opt/tracking-system`.

### Recent tuning (human tracking)

- `fused_mode: true` — one fused dot per person on the plan.
- `cluster_distance_px: 100` — merges same person across four K/WZ cameras.
- `hold_sec: 0` — disabled for people to avoid ghost dots when moving.
- Geom filters skip partial bboxes (e.g. person leaning on kitchen counter).
- Chair-test mode (class 56) remains available for calibration validation.

| Camera | Zone | IP | Rotate |
|--------|------|-----|--------|
| cam_kwz_sw | K/WZ | 192.168.178.75 | 0° |
| cam_kwz_nw | K/WZ | 192.168.178.76 | 0° |
| cam_kwz_ne | K/WZ | 192.168.178.72 | 0° |
| cam_kwz_se | K/WZ | 192.168.178.73 | 0° |
| cam_yoga_ne | Yoga | 192.168.178.71 | 180° |
| cam_yoga_se | Yoga | 192.168.178.70 | 180° |
| cam_hallway_n | Hallway | 192.168.178.74 | 90° |

**Sanity check:** `python tools/probe_rtsp.py` (all 7 streams must pass on home LAN).

---

## 3. Privacy room tracking (SZ + BZ)

Bedroom (SZ) and bathroom (BZ) have **no cameras** — privacy policy. Presence and fall detection use ceiling-mounted **MLX90640** thermal grids (32×24, non-imaging) on Olimex ESP32-POE-ISO nodes, MQTT to the Pi.

### Separate thermal pipeline

- Runs as: `python -m tracking_engine.thermal.run` (systemd: `tracking-thermal.service`).
- Subscribes to MQTT topics `home/sz/*` and `home/bz/*` (thermal frames, door, leak).
- Detects heat-blob posture: rapid vertical→horizontal transition + stillness → fall event.
- **SZ:** bed/rest zone can suppress false positives at bedtime.
- **BZ:** water-leak sensor boosts confidence for shower-slip falls.
- Live thermal positions POST to Maro (same positions API); fall events to `/tracking/events`.
- Geometry from `camera_placement_plan/output/cameras_config.json` privacy polygons.

### Hardware (see [BOM.md](BOM.md))

- 2× MLX90640 + 2× ESP32-POE-ISO (SZ and BZ).
- 2× wired door reed switches; 1× BZ water-leak probe.
- Ceiling mounts documented in the placement plan (thermal footprints in purple/pink).

---

## 4. BEV camera placement (Phase 0)

**Bird's Eye View (BEV)** is the next major upgrade: instead of detecting on seven separate camera views and merging dots, the system will warp each camera onto the floor plane, stitch **one top-down image per zone**, and detect once per zone. Same Maro dashboard; cleaner tracking.

| Zone | Cameras | BEV output |
|------|---------|------------|
| K/WZ | 4 corner cameras | 4 views → 1 stitched floor map |
| Yoga | 2 cameras | 2 views → 1 stitched floor map |
| Hallway | 1 camera | 1 warped floor map |

### Phase 0 — layout freeze (in progress)

- Physical mounts stay at structural corners — no full relocation planned.
- Three placement variants generated for on-site comparison:
  - **Variant A** — baseline (current as-built aim).
  - **Variant B** — slightly more floor overlap for K/WZ + Yoga (yaw/tilt only).
  - **Variant C** — hallway + Yoga/hallway handover focus.
- Diagrams: `camera_placement_plan/output/camera_placement_plan_variant_{a,b,c}.png`
- Site checklist: [layout_freeze_checklist.md](layout_freeze_checklist.md)
- After the trial: freeze one variant, recalibrate all cameras, then build BEV software ([roadmap.md](roadmap.md)).
- **Rule:** no camera or furniture moves until post-BEV recalibration window.

### BEV placement vs today

- Overlap between neighboring cameras is **good** for BEV (needed for stitching), not bad.
- Main changes are **software** (warp + stitch), not moving all seven brackets.
- Optional small re-aim (±5–10° yaw/tilt) only if the site checklist finds gaps.

---

## 5. What happens next

1. On-site: score variants A/B/C, pick winner, document in [layout_freeze.md](layout_freeze.md).
2. Re-run `probe_rtsp.py` on Pi — all 7 streams OK.
3. Full homography recalibration after layout is frozen.
4. Implement BEV warp/stitch per [roadmap.md](roadmap.md) (Phases 1–11).

---

## Reference files in the repository

| File | Purpose |
|------|---------|
| [roadmap.md](roadmap.md) | BEV implementation phases |
| [layout_freeze.md](layout_freeze.md) | Frozen layout record (after site trial) |
| [layout_freeze_checklist.md](layout_freeze_checklist.md) | On-site scoring sheet |
| `tracking_engine/config.multi_camera.yaml` | Runtime streams + fusion |
| `camera_placement_plan/generate_camera_plan.py` | Authoritative camera poses |
| [BOM.md](BOM.md) | Hardware bill of materials |
