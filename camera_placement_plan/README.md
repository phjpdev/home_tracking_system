# Camera Placement Plan — Phase 1.5 (7 cameras + privacy-room thermal sensors)

**Status:** Camera poses and trackable polygons in [generate_camera_plan.py](generate_camera_plan.py) are the **authoritative source** (including the refined K/WZ cam 4 mount, Yoga/Hallway `TRACKABLE_AREAS_MM`, and `ROOM_BOUNDS_MM`). Production camera SKU is locked to the OEM PoE board described in [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md). Privacy-room fall detection uses low-resolution thermal IR because on-site mmWave trials failed in the presence of metal in the wall assembly.

**Camera hardware (production-locked):** OEM PoE IP camera board (HiSilicon-class SoC + Sony image sensor, 1080p+ H.264/H.265 RTSP, IRCUT, IR LEDs, M12 2.8 mm lens). Seven placements plus one spare. See [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md).

**Privacy-room sensors:** MLX90640 (32×24, 55° FOV) thermal IR grid in SZ + BZ on Olimex ESP32-POE-ISO running ESPHome, for non-imaging fall detection (RF-immune). Wired conductive water-leak probe in BZ as a secondary channel. Same FastAPI/MQTT pipeline as the cameras. See [../plan/MASTER_PLAN.md](../plan/MASTER_PLAN.md) section 6 for the migration rationale (AMG8833 8×8 → MLX90640 32×24) and [../esphome/README.md](../esphome/README.md) for the ESP32 firmware.

**Building envelope:** 19.8 m × 10.2 m floor-plan footprint (reference: `ZB Baugruppe TMH2.STEP`), ceiling 3.0 m. Tracked regions include K/WZ, Yoga, and the hallway strip defined in `TRACKABLE_AREAS_MM["Hallway"]`.

---

## What changed since 1.4

1. **Seventh camera (hallway).** Adds re-ID continuity into the corridor that links Yoga to the rest of the floor, instead of stopping at Yoga’s east edge.
2. **Production camera SKU.** AliExpress OEM PoE board (≈ EUR 36 / unit) vs. EUR 65–220 finished consumer models. The placement script is camera-model-agnostic; geometry lives here, hardware metadata in `cameras_config.json`.
3. **Privacy rooms: mmWave → thermal IR.** On-site trials of Aqara FP2 and Apollo R1 failed: metal frame and glazing mullions scatter 24/60 GHz radar. Panasonic AMG8833 (Grid-EYE) 8×8 replaces radar for SZ/BZ; optical, low resolution, no identifiable imagery. Details in [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md).
4. **`Hallway` in `ROOM_BOUNDS_MM` and `TRACKABLE_AREAS_MM`.** Corridor polygon and cam 7 pose are maintained in code—refine after an as-built survey if the built corridor differs from `floor_plan.png`.

---

## 1. The seven placements (authoritative: `CAMERAS` in generate_camera_plan.py)

These values are copied from the generator script for quick reading. **If they ever disagree, trust `generate_camera_plan.py` and re-run the script.**

| # | Name             | Position (x, y, z) mm | Yaw     | Tilt   | Role |
|---|------------------|----------------------|---------|--------|------|
| 1 | `cam_kwz_sw`     | (2600, 9100, 3000)   | 330 deg | 35 deg | K/WZ — south-west, looks NE |
| 2 | `cam_kwz_nw`     | (2600, 5700, 3000)   |  25 deg | 35 deg | K/WZ — north-west, looks SE |
| 3 | `cam_kwz_ne`     | (6200, 5400, 3000)   | 155 deg | 35 deg | K/WZ — north-east on K/WZ–BZ frame, looks SW into K/WZ + strip toward fireplace |
| 4 | `cam_kwz_se`     | (6400, 7300, 3000)   | 330 deg | 35 deg | K/WZ — middle-east on K/WZ–BZ frame, looks NW into K/WZ + along BZ/SZ strip |
| 5 | `cam_yoga_ne`    | (17800, 5400, 3000)  | 155 deg | 35 deg | Yoga — north-east, looks SW |
| 6 | `cam_yoga_se`    | (14200, 8700, 3000)  | 330 deg | 35 deg | Yoga — south on SZ–Yoga frame, looks NE; stereo with #5 |
| 7 | `cam_hallway_n`  | (14100, 8200, 3000)  | 180 deg | 35 deg | Hallway — looks west along corridor; clip polygon keeps FOV in the trackable strip |

Mount coordinates follow structural ceiling frames on the reference plan. After as-built verification, update `x_mm` / `y_mm` / `yaw_deg` for any camera whose real bracket differs, then regenerate outputs.

Visual output: [output/camera_placement_plan.png](output/camera_placement_plan.png). Machine-readable: [output/cameras_config.json](output/cameras_config.json).

### Yaw / tilt convention (installers)

```
   Origin (0, 0) = NW corner of the floor-plan envelope (top-left of plan).
   +x = east   (right on plan)
   +y = south  (down on plan)
   +z = up     (toward ceiling)
   units = millimetres throughout.

   Envelope        = 19 800 x 10 200 mm   (entire rounded outline, matches STEP file)
   Ceiling         = 3 000 mm above floor
```

**Yaw** (compass-like in this frame):

- 0° = east (+x)
- 90° = south (+y, down on screen)
- 180° = west (−x)
- 270° = north (−y, up on screen)

**Tilt:** degrees below horizontal (positive = looking down). 35° is the working default for a 3 m ceiling.

---

## 2. Trackable area widgets (`TRACKABLE_AREAS_MM`)

Each tracked region is a closed polygon: the floor area the room’s cameras can see. Defined at the top of [generate_camera_plan.py](generate_camera_plan.py). Per-camera FOV wedges are **clipped** to these polygons in the diagram and should match runtime filtering.

- **Outside the polygon:** not drawn; tracking should drop detections outside the bound.
- **Inside:** each wedge extends until it hits the polygon edge (and `FIREPLACE_X_MM` geometry in K/WZ).

### K/WZ — L-shape, fireplace cap at `FIREPLACE_X_MM = 10000`

Vertices (mm), in order:

```
(2500, 9200) → (2500, 5000) → (10000, 5000) → (10000, 7400) → (6300, 7400) → (6300, 9200)
```

- Main block spans roughly x ∈ [2500, 6300], y ∈ [5000, 9200], with the upper arm extending east only to the fireplace plane at x = `FIREPLACE_X_MM`.
- Adjust **`FIREPLACE_X_MM`** alone if the fireplace occluder moves on a revised plan.

### Yoga — rectangle inset from walls

```
(14100, 5300) → (17900, 5300) → (17900, 8800) → (14100, 8800)
```

Cam 5 (NE) and cam 6 (S) cover this rectangle stereoscopically.

### Hallway — corridor strip (single-camera coverage)

```
(14000, 8700) → (10100, 8700) → (10100, 8000) → (14000, 8000)
```

Cam 7 uses **yaw 180°** (west) so the wedge runs along the strip; the polygon removes paint outside the corridor. Update these four vertices together with cam 7’s mount if the as-built hallway differs.

---

## 3. Why this layout

**K/WZ — four cameras; Yoga — two.** Same intent as Phase 1.4: stereo-style coverage in K/WZ, Yoga cameras on the east end facing away from the SZ↔Yoga opening, wedges clipped per room.

**Hallway — one camera (Phase 1.5).** A narrow corridor is covered from one end with a wide horizontal FOV; polygon clipping removes wedge overflow. Handover to Yoga uses cams 5–6 at the Yoga–hallway boundary.

**Mounting:** Long north and south façades are sliding glass; mounts target **structural steel at interior corners** with ceiling drops.

### Privacy rooms — no optical cameras

SZ and BZ use the thermal grid + optional water leak (BZ) only. No RGB inference in those rooms by design.

### Privacy thermal polygons (BZ + SZ) — floor footprints for fall logic

Not the same as optical trackable polygons. Drawn on [output/camera_placement_plan.png](output/camera_placement_plan.png) and exported as `privacy_thermal_zones_mm` in [output/cameras_config.json](output/cameras_config.json).

**BZ (bathroom), mm:**

```
(6400, 7400) → (10000, 7400) → (10000, 8700) → (8500, 8700) → (8500, 9200) → (6400, 9200)
```

**SZ (bedroom), mm — rectangle:**

```
(10100, 5000) → (14200, 5000) → (14200, 8000) → (10100, 8000)
```

### Fall detection logic (thermal + optional water leak)

Implementation: [thermal_fall_detection.py](thermal_fall_detection.py) (`PrivacyThermalFallDetector`). Tunables: `THERMAL_FALL_DETECTION_EXPORT` in [generate_camera_plan.py](generate_camera_plan.py) → `thermal_fall_detection` in `cameras_config.json`.

| Zone | Idea | Timing / gating |
|------|------|-----------------|
| **SZ** | Fall vs intentional lie-down via motion before stillness | Rapid vertical→horizontal within ~1 s vs slow ≥3 s; **30 s** horizontal-motionless confirmation inside polygon; optional rest zone suppresses benign bed/couch events |
| **BZ** | No bed; horizontal floor blob is suspicious | **20 s** stillness ⇒ fall (**medium**); concurrent water-leak signal ⇒ **high** (shower slip) |

BOM and procurement context: [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md).

---

## 4. How to run / regenerate the plan

```bash
cd camera_placement_plan
pip install matplotlib pillow numpy
python generate_camera_plan.py
```

Outputs (`output/`):

- **`camera_placement_plan.png`** — full-width annotated plan (thermal footprints + camera FOVs clipped to trackable polygons).
- **`cameras_config.json`** — `trackable_polygon_mm`, `privacy_thermal_zones_mm`, `thermal_fall_detection`, `privacy_room_sensors`, per-room metadata.

Edit the constants in [generate_camera_plan.py](generate_camera_plan.py), then re-run so PNG and JSON stay in sync.

| To change… | Edit… |
|------------|--------|
| Camera mount / aim | `CAMERAS` (`x_mm`, `y_mm`, `yaw_deg`, …) |
| K/WZ, Yoga, or Hallway trackable polygon | `TRACKABLE_AREAS_MM` |
| Fireplace optical blocker | `FIREPLACE_X_MM` |
| Interior envelope | `INTERIOR_*`, `ROOM_BOUNDS_MM` |
| BZ / SZ thermal floor polygon | `PRIVACY_THERMAL_ZONES_MM` |
| Fall timings / rest zone | `THERMAL_FALL_DETECTION_EXPORT`, `SZ_REST_ZONE_POLYGON_MM` |
| Sensor blurbs in JSON | `privacy_room_sensors` in `export_config()` |

---

## 5. What still benefits from STEP-automation

Goals for `step_pipeline/extract_floor_plan.py` (future):

1. Pixel-to-mm scale from CAD, not manual fit to `floor_plan.png`.
2. Interior walls and frame lines from B-rep for repeatable mounts.
3. Fireplace plane x as geometry, not a single estimated `FIREPLACE_X_MM`.
4. Hallway poly and cam 7 verified against exported polylines.
5. **`sz_rest_zone_polygon_mm`** populated after furniture is marked on the mm plan.

Until that lands, vertices are **reference-plan–aligned** and should be reconciled with any authoritative CAD export.

---

## 6. Open items / next deliverables

- **Hallway + cam 7:** validate on site against steel locations; edit `TRACKABLE_AREAS_MM["Hallway"]` and cam 7 in `CAMERAS`, then regenerate.
- **SZ presence:** AMG8833 blob crossing the threshold of the SZ polygon serves as entry/exit; no door contact required.
- **Smoke tests:** one OEM PoE board and one AMG8833 grid before volume buy ([docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md)).
- **`tracking_demo/`** — single-camera re-ID demo ([tracking_demo/README.md](../tracking_demo/README.md)).
- **`tracking_engine/`** — RTSP MVP ([tracking_engine/README.md](../tracking_engine/README.md)).
- **`calibration_tool/`** — browser homography for non-expert operators.

---

## 7. Risks (summary)

- **R1 — Real H-FOV vs 66° model.** Measure one unit on wall at 1 m; set `fov_h_deg` in `CAMERAS` from data.
- **R2 — OEM firmware telemetry.** VLAN isolation + OpenIPC flash after smoke test ([docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md)).
- **R3 — IR reach vs room depth.** Confirm night behaviour under real ambient light and exposure.
- **R4 — PoE stream fps.** Substream headroom is usually ample; pipeline tolerates lower fps.
- **R5 — Glass reflections.** Detections outside `trackable_polygon_mm` should be discarded upstream.
- **R6 — `FIREPLACE_X_MM`.** Visual estimate until CAD export replaces it.
- **R7 — Hallway model.** Thin polygon assumes current corridor sketch; update vertices if the built route differs.
- **R8 — 8×8 thermal resolution.** MLX90640 (32×24) is the documented upgrade path if posture gating is marginal.
- **R9 — mmWave unsuitable here.** Metal-heavy walls rule out radar; thermal / PIR / mats remain the alternatives for privacy-preserving presence.
