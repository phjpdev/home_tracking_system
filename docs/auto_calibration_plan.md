# Auto-calibration plan — combined approach

This document is the implementation roadmap for **automatic calibration** and **stable tracking** using:

1. **Fixed landmarks** — floor tiles, LED strips, and (later) production furniture / design-in patterns  
2. **Multi-view geometry** — stereo foot fusion and walking-person self-calibration where cameras overlap  
3. **Privacy rooms** — thermal presence in SZ/BZ (no optical calibration there)

It complements manual [`calibration_maro_plan_px.md`](calibration_maro_plan_px.md) and [`hallway_calibration_client_guide.md`](hallway_calibration_client_guide.md). Those stay as **fallback** until each phase below is proven on site.

---

## 1. Goals (what “done” means)

| Goal | Meaning | Not required |
|------|---------|--------------|
| **Auto map alignment** | Each camera’s homography updates without hand-clicking every landmark | Sub-millimeter CAD accuracy |
| **Stable person dot** | One fused position per person where two cameras overlap | Perfect X/Y in architect mm |
| **Relation calibration** | Correct **room/zone** and smooth movement on the Maro plan | Global mm truth before real building prototype |
| **Production-ready** | Same home model → same landmark catalog → same software | ArUco stickers in final homes |
| **Drift handling** | Nightly or on-demand recheck; alert if residual exceeds threshold | 24/7 LED blink visible to residents |

---

## 2. Two layers (always combine both)

```text
  LAYER A — MAP (where cameras sit on the floor plan)
  ─────────────────────────────────────────────────
  Tiles · LED strip indices · Furniture / design landmarks (production)
           │
           ▼
  camera_calibrations.json  (per-cam H: pixel → maro_floorplan_px)

  LAYER B — PERSON (where the resident is right now)
  ───────────────────────────────────────────────────
  Stereo foot fusion (overlap zones) · Zone polygons · Optional Re-ID
           │
           ▼
  POST persons[] to Maro (zone + x,y + confidence + source)
```

**Rule:** Layer A must be good enough before Layer B fusion across many cameras; otherwise stereo and median-fusion both amplify error.

---

## 3. Per-room strategy

| Area | Cameras | Layer A (landmarks) | Layer B (person) | Notes |
|------|---------|----------------------|------------------|-------|
| **Yoga** | `cam_yoga_ne` + `cam_yoga_se` | LED strip on east wall; tile grid on floor | **Stereo pair (pilot)** | Clean overlap; best first stereo test |
| **K/WZ** | 4 corners | Tiles + LEDs + door/lamp landmarks | Stereo between **adjacent** pairs only; zone-primary elsewhere | Do not median-fuse all four until pairs are calibrated |
| **Hallway** | `cam_hallway_n` | Tiles (already used for distortion lines) + LED if strip visible | Single camera; **no stereo** until extra cam overlaps mouth | Undistort before homography |
| **SZ / BZ** | None | Thermal polygon fixed on plan | Zone + fall events only | No homography for optical |

---

## 4. Phased roadmap

### Phase 0 — Stabilize today (1 week, no new algorithms)

**Problem:** More cameras feel worse because `plan_fusion` median-averages misaligned homographies.

**Actions:**

- [ ] Per camera: finish Maro landmark or LED+Line cal; target mean residual ≤ 5 px ([`calibration_day_handover.md`](calibration_day_handover.md)).
- [ ] Hallway: intrinsics via [`tools/calibrate_distortion_lines.py`](../tools/calibrate_distortion_lines.py), then homography ([`hallway_calibration_client_guide.md`](hallway_calibration_client_guide.md)).
- [ ] Config: set `tracking.plan_fusion.enabled: false` OR raise cluster distance until Phase 2 stereo is live.
- [ ] Output: treat **`zone`** as primary; plan `x,y` as secondary with confidence flag.

**Exit criteria:** Each enabled camera passes residual gate alone; Maro dot stable in **one** camera view per room.

---

### Phase 1 — LED auto-landmarks (2–3 weeks)

**Uses:** Existing `calibrate_web/led_marker.py` geometry (`strips.json`, 10 cm logical markers).

**Build:**

| Item | Path / module (proposed) |
|------|---------------------------|
| Sequential LED calibrator | `tools/calibrate_led_sequence.py` |
| Integration hook | `tracking_engine/calibration/auto_led.py` |
| Optional UI button | `calibrate_web` — “Run LED auto-cal” |

**Flow:**

1. Dim room; fix camera exposure if possible.
2. For each strip in `maro_cache/strips.json`: light marker `k`, then `k+5`, … via Art-Net (reuse `LedMarker`).
3. On each camera still: detect brightest blob along expected strip direction → image point.
4. Match blob index `k` → known plan mm/px from `StripGeom.marker_position_mm(k)`.
5. RANSAC homography per camera; write `camera_calibrations.json`.
6. Log mean residual; refuse save if &gt; 8 px.

**Exit criteria:** Hallway + Yoga calibrate without hand-clicking LED points; residuals within hand-cal tolerance.

**Fallback:** Manual `calibrate_web` unchanged.

---

### Phase 2 — Tile grid assist (2–3 weeks, parallel-friendly)

**Uses:** Measured tile size + grout lines (client measures once per site; fixed in production datasheet).

**Build:**

| Item | Path (proposed) |
|------|-----------------|
| Tile line detector | `tools/calibrate_tile_grid.py` |
| Refinement step | Called after LED or landmark homography |

**Flow:**

1. Undistort frame (`camera_intrinsics.json` where present).
2. Detect line segments (LSD or Hough); cluster into two orthogonal families.
3. Enforce known tile pitch (mm) → metric scale on floor plane.
4. Refine homography or report scale drift vs stored cal.

**Exit criteria:** Tile-only residual within ~10% of LED cal in hallway; improves scale in K/WZ floor areas with visible grid.

**Limits:** Rugs, strong reflections, non-rectangular rooms — tile assist **refines**, does not replace LED/furniture anchors.

---

### Phase 3 — Stereo foot fusion (3–4 weeks)

**Uses:** Overlap documented in [`camera_placement_plan/README.md`](../camera_placement_plan/README.md) (Yoga 5+6, K/WZ pairs).

**Build:**

| Item | Path (proposed) |
|------|-----------------|
| Stereo triangulation | `tracking_engine/pipeline/stereo_foot.py` |
| Pair config | `tracking_engine/config.stereo_pairs.yaml` or section in `config.multi_camera.yaml` |
| Fusion policy | Extend `plan_fusion.py` — fuse only when stereo agrees; else best single cam by zone |

**Flow (runtime):**

1. Per tick: detect person foot point (bottom center of bbox) in cam A and cam B.
2. Match pair: Re-ID `global_id` OR proximity in time + similar bbox height.
3. Triangulate ray–floor plane (z = 0) → one `(x_plan, y_plan)`.
4. Reprojection error &gt; threshold → drop to single-camera view for that person.

**Exit criteria:** Yoga room: walking path smoother than median of two homographies; fewer jumps when both cams see subject.

**Depends on:** Phase 0 per-camera H reasonable; OSNet Re-ID (Phase A.5 in MASTER_PLAN) helps matching but not strictly required for single resident.

---

### Phase 4 — Walking self-calibration (4–6 weeks)

**Purpose:** Cameras **calibrate each other**; reduce per-camera manual drift.

**Build:**

| Item | Path (proposed) |
|------|-----------------|
| Offline BA / pose solver | `tools/solve_multiview_calibration.py` |
| Correspondence collector | Log `(cam, foot_px, timestamp)` when stereo match confident |
| Nightly job | `deploy/scripts/nightly_cal_check.sh` (optional systemd timer) |

**Flow:**

1. Collect 10–30 min of normal walking in overlap zones (or guided once).
2. Bundle-adjust camera extrinsics relative to floor plane; keep one **anchor** (LED index 0, door segment, or single plan landmark).
3. Derive updated per-camera `H` to Maro plan px; compare to previous; save if improvement &gt; 1 px mean and &lt; max jump cap.

**Exit criteria:** After furniture nudge (mock-up), walking session restores residuals without opening calibrate_web.

---

### Phase 5 — Production landmark catalog (series homes)

**Purpose:** Client vision — furniture, speaker, trim patterns at **known CAD coordinates**.

**Build:**

| Item | Path (proposed) |
|------|-----------------|
| Catalog schema | `tracking_engine/calibration/landmark_catalog.json` |
| Detectors | Per-SKU: AprilTag / template / feature cluster |
| Design patterns | Optional custom dictionary (not consumer ArUco stickers) |

**Catalog entry (example):**

```json
{
  "id": "speaker_kwz_v1",
  "plan_px": [1200, 890],
  "rooms": ["K/WZ"],
  "detector": "apriltag:36h11:17",
  "min_width_px": 40
}
```

**Flow:**

1. Factory/home model ships with `landmark_catalog.json` for that SKU.
2. On boot or nightly: each camera runs detectors → new correspondences → refine `H`.
3. Stereo + catalog together; LEDs used for install verification only.

**Exit criteria:** New unit of same model calibrates without installer clicks; ArUco not required in field.

---

### Phase 6 — Thermal + optical state machine (when MLX90640 live)

**Not homography** — merge at application level:

| Signal | Output |
|--------|--------|
| Thermal `in_SZ` / `in_BZ` | Force zone; suppress optical dot near privacy boundary |
| Optical stereo | Track in K/WZ, Yoga, Hallway |
| Conflict | Thermal wins for zone; optical wins for plan position outside privacy polygons |

See [`camera_placement_plan/thermal_fall_detection.py`](../camera_placement_plan/thermal_fall_detection.py),
MASTER_PLAN Phase C, and [`privacy_zone_production_runbook.md`](privacy_zone_production_runbook.md).

---

## 5. Configuration and data files

| File | Role |
|------|------|
| `tracking_engine/calibration/camera_calibrations.json` | Per-camera `H`, `world_points_plan_px` |
| `tracking_engine/calibration/camera_intrinsics.json` | Distortion (hallway / wide lens) |
| `tracking_engine/calibration/maro_cache/strips.json` | LED paths for Phase 1 |
| `tracking_engine/calibration/landmark_catalog.json` | Phase 5 production SKUs (new) |
| `tracking_engine/config.multi_camera.yaml` | `plan_fusion`, stereo pairs, auto-cal flags |
| `camera_placement_plan/output/cameras_config.json` | Zones, thermal polygons, room names |

**Proposed new config block** (`config.multi_camera.yaml`):

```yaml
auto_calibration:
  enabled: true
  led_sequence: true
  tile_refinement: true
  nightly_check: false          # enable after Phase 4 stable
  max_mean_residual_px: 8.0
stereo:
  enabled: true
  pairs:
    - [cam_yoga_ne, cam_yoga_se]
    # add K/WZ adjacent pairs after Yoga pilot
  max_reproj_error_px: 12.0
```

---

## 6. Success metrics

| Metric | Target | How to measure |
|--------|--------|----------------|
| Homography mean residual | ≤ 5 px (≤ 8 px save gate) | `calibrate_web` stats / auto-cal log |
| Stereo reprojection | ≤ 12 px | Log per matched pair |
| Zone correctness | ≥ 95% room transitions | Manual walk test script |
| Dot jitter (standing still) | &lt; 30 px σ on plan | 60 s stationary, log POST |
| Cal time (installer) | &lt; 15 min LED auto + verify | Stopwatch vs manual landmark |
| Drift after 7 days | &lt; 5 px mean without human | Nightly check job |

---

## 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| LED not visible to all cams | Per-room strips; multiple indices; manual fallback |
| Tile detection fails (rug/shadow) | Tile refines only; never sole anchor |
| Stereo mismatch (two people) | Re-ID + zone gating; reject high reprojection |
| Pi CPU for nightly BA | Run BA offline on laptop via SSH pull, or weekly not nightly |
| Mock-up furniture moves | Rely Phase 1–4; defer Phase 5 until CAD locked |
| More cameras worse | Disable global median fusion until Phase 3 per pair |

---

## 8. Suggested build order (summary)

```text
Phase 0  Stabilize + zone-first outputs
   ↓
Phase 1  LED auto-cal  ─────────────┐
   ↓                                ├── Layer A (map)
Phase 2  Tile refinement  ─────────┘
   ↓
Phase 3  Stereo foot fusion  ────── Layer B (person)
   ↓
Phase 4  Walking self-cal (multi-view BA)
   ↓
Phase 5  Production landmark catalog
   ↓
Phase 6  Thermal zone merge (parallel when hardware arrives)
```

**First demo for client:** Phase 1 (LED auto) + Phase 3 (Yoga stereo walk) — visible win without waiting for production furniture.

---

## 9. Client message (short)

We combine **your** long-term idea (tiles, LEDs, built-in design marks at known plan positions) with **multi-camera stereo** so person tracking is stable where two cameras overlap. ArUco stays an optional engineer tool only. Bathroom and bedroom use **thermal zones**, not cameras. The mock-up gets LED + tile + stereo first; series production adds the furniture/design catalog on top.

---

## 10. Related docs

- [`calibration_maro_plan_px.md`](calibration_maro_plan_px.md) — manual landmark UI  
- [`calibration_day_handover.md`](calibration_day_handover.md) — operator checklist  
- [`hallway_calibration_client_guide.md`](hallway_calibration_client_guide.md) — distortion + LED line  
- [`camera_placement_plan/README.md`](../camera_placement_plan/README.md) — stereo layout intent  
- [`plan/MASTER_PLAN.md`](../plan/MASTER_PLAN.md) — Re-ID, thermal, production phases  

---

## 11. MVP operator flow (implemented)

### One-time setup (LED strips)

1. Copy or sync LED config into Maro cache (if not already present):
   - `tracking_engine/calibration/maro_cache/strips.json`
   - `tracking_engine/calibration/maro_cache/artnet_mapping.json`
   - Defaults are seeded from `maro-light-tools/meter_test/config/` on first run.
2. Ensure Maro API is reachable and ODE/Pixelator can drive the strips (dim ambient light).

### Auto-calibrate map (Layer A)

**CLI (Pi or dev machine on LAN):**

```bash
cd /opt/tracking-system
/opt/tracking-system/.venv/bin/python tools/calibrate_led_sequence.py \
  --config tracking_engine/config.multi_camera.yaml \
  --cameras cam_yoga_ne,cam_yoga_se,cam_hallway_n
```

Optional: `--dry-run` (no Art-Net / no file write), `--refine-tiles --tile-mm 600`.

**Calibration UI:**

1. Open `calibrate_web` → click **LED auto-cal** (runs in background ~2–5 min).
2. Poll completes → recapture + recompute shown in UI.

### Deploy on Pi (git pull — do not edit `/opt` only)

```bash
cd ~/home_tracking_system && git pull
sudo bash deploy/scripts/install.sh
sudo systemctl restart tracking-engine tracking-calibrate-web
```

Maro must be running separately: `cd ~/maro-clean && .venv/bin/uvicorn src.maro.web.app:app --host 0.0.0.0 --port 8420`

### Restart tracking

```bash
sudo systemctl restart tracking-engine
```

### Runtime stereo (Layer B)

With `stereo.enabled: true` and pair `[cam_yoga_ne, cam_yoga_se]` in
`config.multi_camera.yaml`, fused POSTs prefer stereo rows when both cameras
see the same person. Check `position_source` / `cal_confidence` on each person.

### Fallback

Manual landmark calibration via `calibrate_web` remains available if LED auto-cal
fails (residual &gt; 8 px or &lt; 6 markers detected).

---

*Document version: 1.1 — MVP operator flow added.*
