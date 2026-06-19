# Phase 0 — Layout freeze site checklist

Use this sheet during the on-site A/B/C camera placement trial. Print the three variant PNGs from `camera_placement_plan/output/camera_placement_plan_variant_{a,b,c}.png`.

**BEV scoring criteria (from [roadmap.md](roadmap.md)):**

| Criterion | Target |
|-----------|--------|
| Floor corner visibility | Every walkable corner in each zone seen by ≥1 camera |
| Overlap | ≥30% floor area seen by ≥2 cameras in adjacent views |
| Floor in frame | ≥60% of substream (704×576) is floor, not ceiling/walls |
| Occlusion | Island, fireplace, tall furniture not blocking main paths |
| Stitch feasibility | Adjacent cameras share floor features (tiles, rug edges) |
| Privacy | No optical view into SZ/BZ |

---

## 1. RTSP + still capture (per variant)

On the Pi (or laptop on `192.168.178.0/24`):

```bash
cd /opt/tracking-system   # or repo root on laptop
python tools/probe_rtsp.py --save-stills stills/variant_a/
```

For each variant **A → B → C**:

1. Physically re-aim cameras per the printed PNG (yaw/tilt only; mounts stay fixed).
2. Run probe with a distinct stills folder (`variant_a/`, `variant_b/`, `variant_c/`).
3. Record per camera:

| Camera | Variant | Resolution | rotate (0/90/180/270) | Floor fraction (>60%?) | Notes |
|--------|---------|------------|------------------------|-------------------------|-------|
| cam_kwz_sw | | | | | |
| cam_kwz_nw | | | | | |
| cam_kwz_ne | | | | | |
| cam_kwz_se | | | | | |
| cam_yoga_ne | | | | | |
| cam_yoga_se | | | | | |
| cam_hallway_n | | | | | |

Update `tracking_engine/config.multi_camera.yaml` `streams[].rotate` if any value differs from the current file after the winning variant is chosen.

**Variant reference (yaw / tilt from generator):**

| Camera | A (baseline) | B (overlap) | C (hallway) |
|--------|--------------|-------------|-------------|
| cam_kwz_sw | 330 / 35 | 335 / 40 | 330 / 35 |
| cam_kwz_nw | 25 / 35 | 30 / 40 | 25 / 35 |
| cam_kwz_ne | 155 / 35 | 150 / 40 | 155 / 35 |
| cam_kwz_se | 330 / 35 | 325 / 40 | 330 / 35 |
| cam_yoga_ne | 155 / 35 | 160 / 40 | 155 / 35 |
| cam_yoga_se | 330 / 35 | 335 / 40 | 335 / 35 |
| cam_hallway_n | 180 / 35 | 180 / 35 | 175 / 42 |

Regenerate PNGs/JSON anytime: `python camera_placement_plan/generate_camera_plan.py --variant all`

---

## 2. Per-zone scoring

### K/WZ (4 cameras)

| Check | A | B | C | Winner notes |
|-------|---|---|---|--------------|
| All 4 L-shape corners visible on floor | | | | |
| Kitchen counter: floor visible in front of counters | | | | |
| Dining/living: no large blind spot behind sofa/island | | | | |
| Same floor feature (rug/tile) seen by ≥2 cameras | | | | |
| No SZ/BZ privacy bleed in preview | | | | |

### Hallway (1 camera)

| Check | A | B | C | Winner notes |
|-------|---|---|---|--------------|
| Full corridor floor end-to-end | | | | |
| Both corridor ends (Yoga + K/WZ) visible | | | | |
| Person at mid-corridor fills ≥5% frame height at 3 m | | | | |

### Yoga (2 cameras)

| Check | A | B | C | Winner notes |
|-------|---|---|---|--------------|
| Full rectangle floor visible | | | | |
| ≥30% overlap in center | | | | |
| Handoff to hallway visible from ≥1 camera | | | | |

---

## 3. Furniture freeze

Document **final** furniture positions that affect line-of-sight (one wide photo per zone):

- [ ] Kitchen island / counters
- [ ] Dining chairs / table
- [ ] Yoga mats / equipment
- [ ] Any new radar sensor housings

Photo directory: `docs/site_photos/YYYY-MM-DD/` (create on site).

---

## 4. Pick winner

| Field | Value |
|-------|-------|
| Date | |
| Chosen variant | A / B / C / hybrid (describe) |
| Per-camera yaw/tilt adjustments from diagram | |
| Mount height changes (cm) | |
| Final `rotate` per stream | |

**Hybrid example:** Variant B for K/WZ + Yoga, Variant C for hallway.

After selection, update [layout_freeze.md](layout_freeze.md) and run the repo freeze steps in that doc.

---

## 5. Phase 0 exit gate — probe all streams

On the Pi with the **winning** physical aim and `config.multi_camera.yaml` rotate values:

```bash
python tools/probe_rtsp.py --frames 30 --timeout 10
```

Exit code **0** on all 7 enabled streams required before Phase 1. Save stills for the record:

```bash
python tools/probe_rtsp.py --save-stills stills/layout_frozen/
```

Do **not** start homography recalibration or BEV implementation until this gate passes.
