# Layout freeze record — Phase 0

> **Status: PENDING SITE TRIAL** — Compare variants A/B/C on site using [layout_freeze_checklist.md](layout_freeze_checklist.md), then fill this document and merge the winner into the generator.

## Summary

| Field | Value |
|-------|-------|
| Freeze date | _TBD_ |
| Chosen variant | _A / B / C / hybrid_ |
| Generator commit | _git hash after freeze commit_ |
| Placement PNG | `camera_placement_plan/output/camera_placement_plan.png` |
| JSON config | `camera_placement_plan/output/cameras_config.json` |

## No-move rule

**No camera repositioning, bracket height changes, or furniture moves that affect line-of-sight** until BEV Phase 5 recalibration is complete and signed off. Homography in `tracking_engine/calibration/camera_calibrations.json` is invalid after any layout change.

---

## Camera poses (fill after site trial)

Baseline below is **Variant A** (current as-built). Replace with measured values after the winner is installed.

| Camera | IP | Mount (x, y, z mm) | yaw° | tilt° | rotate° | Mount height (cm) |
|--------|-----|-------------------|------|-------|---------|-------------------|
| cam_kwz_sw | 192.168.178.75 | 2600, 9100, 3000 | 330 | 35 | 0 | 245 |
| cam_kwz_nw | 192.168.178.76 | 2600, 5700, 3000 | 25 | 35 | 0 | 246 |
| cam_kwz_ne | 192.168.178.72 | 6200, 5400, 3000 | 155 | 35 | 0 | 245 |
| cam_kwz_se | 192.168.178.73 | 6400, 7300, 3000 | 330 | 35 | 0 | 245 |
| cam_yoga_ne | 192.168.178.71 | 17800, 5400, 3000 | 155 | 35 | 180 | 245 |
| cam_yoga_se | 192.168.178.70 | 14200, 8700, 3000 | 330 | 35 | 180 | 246 |
| cam_hallway_n | 192.168.178.74 | 14100, 8200, 3000 | 180 | 35 | 90 | 246 |

Variant diagrams for comparison:

- `camera_placement_plan/output/camera_placement_plan_variant_a.png` — baseline
- `camera_placement_plan/output/camera_placement_plan_variant_b.png` — higher K/WZ + Yoga overlap
- `camera_placement_plan/output/camera_placement_plan_variant_c.png` — hallway + Yoga/hallway handover

---

## Furniture (line-of-sight)

| Zone | Notes | Photo |
|------|-------|-------|
| K/WZ | _island, dining, sofa_ | |
| Hallway | | |
| Yoga | _mats, equipment_ | |

Photos: `docs/site_photos/YYYY-MM-DD/`

---

## Post-trial repo steps

1. Merge winning yaw/tilt into `CAMERAS` in `camera_placement_plan/generate_camera_plan.py` (or keep as variant-specific if hybrid).
2. Regenerate: `python camera_placement_plan/generate_camera_plan.py --variant all --write-default`
3. Update `tracking_engine/config.multi_camera.yaml` — set `# LAYOUT_FROZEN: YYYY-MM-DD variant X` and final `streams[].rotate`.
4. Fill `as_built_measurements_2026_05_21.layout_freeze` in exported JSON.
5. Commit: `layout: freeze variant X YYYY-MM-DD`
6. Run on Pi: `python tools/probe_rtsp.py` — all 7 streams pass (exit 0).

---

## Validation log

| Gate | Date | Result | Notes |
|------|------|--------|-------|
| probe_rtsp.py (7 streams) | | **Run on Pi/LAN** | Off-LAN probe times out; see checklist §5 |
| Stills match PNG wedge direction | | | |
| cameras_config.json ↔ config.multi_camera.yaml names | | | |
