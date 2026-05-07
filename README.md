# Camera Placement Plan — Phase 1.5 (7 cameras + privacy-room thermal sensors)

**Status:** Camera positions locked by client (cams 1–6 from v1.4, plus a new cam 7 in the hallway behind Yoga). Production camera SKU approved (OEM PoE board). Privacy-room fall detection moved from mmWave radar to a low-resolution thermal IR sensor after on-site mmWave testing failed due to metal in the walls.
**Camera hardware (production-locked):** OEM PoE IP camera board (HiSilicon-class SoC + Sony image sensor, 1080p+ H.264/H.265 RTSP, IRCUT, IR LEDs, M12 2.8 mm lens). 7 placements + 1 spare. See [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md).
**Privacy-room sensors:** Panasonic AMG8833 8×8 thermal IR grid in SZ + BZ for non-imaging fall detection (RF-immune). Aqara water-leak sensor in BZ as a secondary channel. Same FastAPI/MQTT pipeline as the cameras.
**Building envelope:** 19.8 m × 10.2 m floor-plan footprint (verified against `ZB Baugruppe TMH2.STEP`), ceiling 3.0 m. Tracked area now includes the upper hallway behind Yoga in addition to K/WZ + Yoga.

---

## What changed since 1.4

1. **+1 camera in the hallway behind Yoga (cam 7).** Client requirement: re-ID continuity must extend into the corridor connecting Yoga to the rest of the home, not stop at Yoga's east wall.
2. **Production camera SKU approved.** Going with the AliExpress OEM PoE camera board (≈ EUR 36 / unit) over the EUR 65–220 finished consumer cameras. The placement script is camera-model-agnostic so the geometry is unchanged; only the `sensor` block in `cameras_config.json` is updated.
3. **Fall detection in privacy rooms switched from mmWave radar → thermal IR grid.** Client tested Aqara FP2 and Apollo R1 on site; both failed. Cause: the building has metal in the walls (frame structure + glazing mullions) which scatters 24/60 GHz radar so badly the sensors are unusable. Replacement is a Panasonic AMG8833 (Grid-EYE) 8×8 thermal sensor: optical, not RF, so metal walls don't matter; 64-pixel resolution means there's nothing identifiable in the data. Detail in [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md) (privacy-room sensors).
4. **`Hallway` added to `ROOM_BOUNDS_MM` and `TRACKABLE_AREAS_MM`.** Coordinates are a placeholder until the client confirms the hallway's exact extents on the floor plan.

---

## 1. The 7 placements (Phase 1.5, locked except cam 7)

| # | Name             | Position (x, y, z) mm | Yaw     | Tilt   | Role                                                       |
|---|------------------|----------------------|---------|--------|------------------------------------------------------------|
| 1 | `cam_kwz_sw`     | (2500, 8600, 3000)   | 330 deg | 35 deg | K/WZ tracking — south-west, looks NE-ish                   |
| 2 | `cam_kwz_nw`     | (2500, 5200, 3000)   |  25 deg | 35 deg | K/WZ tracking — north-west, looks SE-ish                   |
| 3 | `cam_kwz_ne`     | (6200, 4900, 3000)   | 155 deg | 35 deg | K/WZ tracking — north-east on K/WZ-BZ frame, looks SW into K/WZ + east toward fireplace |
| 4 | `cam_kwz_se`     | (6300, 6400, 3000)   | 330 deg | 35 deg | K/WZ tracking — middle-east on K/WZ-BZ frame, looks NE toward fireplace |
| 5 | `cam_yoga_ne`    | (17800, 4900, 3000)  | 155 deg | 35 deg | Yoga tracking — north-east, looks SW across Yoga           |
| 6 | `cam_yoga_se`    | (14200, 8200, 3000)  | 330 deg | 35 deg | Yoga tracking — south on SZ-Yoga frame, looks NE; stereo with #5 |
| 7 | `cam_hallway_n`  | (17800, 3500, 3000)  | 180 deg | 35 deg | **Hallway tracking — east end of corridor behind Yoga, looks W along the long axis (PLACEHOLDER position; confirm with client)** |

Cams 1–6 are unchanged from Phase 1.4 (positions hand-tuned by the client against the actual structural frames). Cam 7 is **a placeholder** — the position above places it at the east end of the corridor on the structural frame between Yoga and the building's east wall. If the actual frame is somewhere else, only the `(x_mm, y_mm, yaw_deg)` for cam 7 needs to change; everything else regenerates from that.

Visualised in [output/camera_placement_plan.png](output/camera_placement_plan.png), exported as machine-readable [output/cameras_config.json](output/cameras_config.json).

### Yaw / tilt convention (re-stated for the installer)

```
   Origin (0, 0) = NW corner of the floor-plan envelope (top-left of plan).
   +x = east   (right on plan)
   +y = south  (down on plan)
   +z = up     (toward ceiling)
   units = millimetres throughout.

   Envelope        = 19 800 x 10 200 mm   (entire rounded outline, matches STEP file)
   Ceiling         = 3 000 mm above floor
```

**Yaw** is compass-like in this frame:
- 0 deg = facing east (+x)
- 90 deg = facing south (+y, down on screen)
- 180 deg = facing west (-x)
- 270 deg = facing north (-y, up on screen)

**Tilt** is degrees below horizontal (positive = looking down). 35 deg is the tracking sweet spot for a 3 m ceiling.

---

## 2. Trackable area widgets

Each tracked area has an explicit polygon describing what its cameras can physically see. The polygons are defined in `TRACKABLE_AREAS_MM` near the top of [generate_camera_plan.py](generate_camera_plan.py); per-camera FOV wedges are clipped to them so:
- **outside the polygon** → nothing is drawn (and the runtime engine drops detections that fall outside)
- **inside the polygon** → each camera's wedge fan extends as far as its angular FOV permits, against the polygon edge

### K/WZ trackable area — L-shape, stops at the fireplace

```
(2400, 8700) → (2500, 4500) → (10000, 4500) → (10000, 6500) → (6300, 6500) → (6300, 8700)
```

- Vertical stem (the K/WZ rectangle proper): x ∈ [2400, 6300], y ∈ [4500, 8700].
- Horizontal arm (upper BZ strip the cameras peek into): x ∈ [6300, **10000**], y ∈ [4500, 6500].
- The arm stops at the fireplace at x ≈ 10000 mm, which is a solid object that camera light cannot pass through. Anything east of the fireplace (i.e. SZ proper) is therefore dark to all K/WZ cameras.
- The single named constant `FIREPLACE_X_MM = 10000` controls the stop position; if the fireplace's east face is actually at a different x, change just that one number.

### Yoga trackable area — rectangle inset from the walls

```
(14100, 4800) → (17900, 4800) → (17900, 8300) → (14100, 8300)
```

- Slightly inset from the room walls so the polygon represents physically reachable floor.
- Cam 5 sits at the NE corner and fans SW; cam 6 sits at the south on the SZ-Yoga frame and fans NE — together they cover the rectangle stereoscopically.

### Hallway (behind Yoga) trackable area — rectangle, single-camera coverage

```
(12000, 2500) → (17900, 2500) → (17900, 4500) → (12000, 4500)
```

- ~5.7 m long (E–W) × ~2 m deep (N–S). Cam 7 at the east end fans west along the long axis; the polygon clip keeps the wedge inside the corridor.
- **Coordinates are a placeholder.** Verify against the floor plan: the corridor I'm assuming runs north of Yoga's north wall in the upper interior strip. If "the hallway behind Yoga" actually means the strip east of Yoga (between Yoga and the east end of the building) or somewhere else, edit the four coordinates above and the cam-7 position together.

---

## 3. Why this layout

**K/WZ — 4 cameras, Yoga — 2 cameras** — unchanged from Phase 1.4. Reasoning is the same: K/WZ corner-to-corner stereo coverage, Yoga east-side cameras that physically face away from the SZ↔Yoga wall, all wedges clipped to their polygons.

**Hallway — 1 camera (new in 1.5)**

The corridor behind Yoga is long and narrow (≈ 5.7 m × 2 m), so a single wide-FOV camera at one end works better than two opposing cameras would. Mounting at the east end of the corridor and aiming west:
- The 66° angular FOV opens up across the corridor's entire length.
- The wedge fan beyond ~3 m exceeds the corridor's 2 m depth — the polygon clip discards the parts that fall outside the corridor walls.
- Re-ID handover with cams 5/6 (Yoga) is at the corridor's south wall, where everyone enters/leaves.

**Mounting points**

The long N + S walls of the building are continuous sliding glass. The only solid wall fixing point on the room perimeter is the **structural steel frame between glass panels at every interior corner / room divider**. All 7 cameras mount on a ceiling drop bracket bolted to one of those frames.

### Privacy rooms — no cameras, thermal sensors instead

SZ + BZ stay camera-free (privacy hard requirement). Fall detection there now uses a low-resolution thermal IR grid:

| Sensor                             | Where  | Why                                                  |
|------------------------------------|--------|------------------------------------------------------|
| Panasonic AMG8833 (Grid-EYE) 8×8   | SZ, BZ | Non-imaging fall detection (heat blob, not picture)  |
| Aqara water-leak sensor (Zigbee)   | BZ     | Secondary channel (fall in shower → wet-floor event) |

The Grid-EYE outputs an 8×8 grid of temperatures — not an image, just 64 temperature readings per frame. There's nothing identifiable in the data (no faces, no clothing, no posture beyond "blob is vertical" vs "blob is horizontal and on the floor"). It's RF-immune, so the metal-in-walls problem that broke Aqara FP2 and Apollo R1 doesn't apply. ESPHome supports the chipset natively, and events flow through the same FastAPI server as the camera detections.

### Privacy thermal polygons (BZ + SZ) — client-defined floor footprints

These are **not** camera trackable areas: they are the floor regions where the low-resolution thermal sensor’s projected blob centroid is evaluated for fall logic. They are drawn as filled overlays on [output/camera_placement_plan.png](output/camera_placement_plan.png) and exported as `privacy_thermal_zones_mm` (and per-room `privacy_thermal_polygon_mm`) in [output/cameras_config.json](output/cameras_config.json).

**BZ (bathroom), mm — counter-clockwise from NW corner of the polygon:**

```
(6400, 7100) → (10000, 7100) → (10000, 8700) → (8500, 8700) → (8500, 9100) → (6400, 9100)
```

**SZ (bedroom), mm — rectangle:**

```
(10100, 5000) → (14200, 5000) → (14200, 8000) → (10100, 8000)
```

### Fall detection logic (thermal + optional water leak)

Runtime implementation: [thermal_fall_detection.py](thermal_fall_detection.py) (`PrivacyThermalFallDetector`). Tunables are mirrored from [generate_camera_plan.py](generate_camera_plan.py) into `thermal_fall_detection` in `cameras_config.json`.

| Zone | Idea | Timing / gating |
|------|------|-----------------|
| **SZ** | Distinguish **fall** vs **going to bed** using pre-stillness trajectory: vertical heat blob → horizontal **within about 1 s** is a strong fall cue; a **slow** transition (about 3 s or longer) is treated as intentional lie-down. | **30 s** confirmation: the blob must stay **horizontal, on floor, inside the SZ polygon, and motionless** (below the configured motion threshold) for the full window. Any stretch or adjustment resets the timer — falls stay still. Optional `sz_rest_zone_polygon_mm` (bed/couch footprint): slow transition or **non-rapid** horizontal rest inside that polygon suppresses an alarm; a **rapid** transition that ends on the bed footprint can still alarm. |
| **BZ** | No bed/couch; a horizontal, on-floor blob that stays still is intrinsically suspicious. | **20 s** stillness on the floor inside the BZ polygon triggers a fall (**medium** confidence). **Water-leak sensor active** at the same time raises confidence to **high** (shower slip: thermal-on-floor + water-on-floor). |

Full narrative and BOM context: [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md).

---

## 4. How to run / regenerate the plan

```bash
pip install matplotlib pillow numpy
python generate_camera_plan.py
```

Outputs (to `output/`):
- `camera_placement_plan.png` — the visual the client signs off on (full-width floor plan, no side panel). Includes semi-transparent **BZ / SZ thermal fall-detection footprints** (not camera FOVs).
- `cameras_config.json` — machine-readable config: `trackable_polygon_mm` for camera rooms, `privacy_thermal_zones_mm`, `thermal_fall_detection` thresholds, `privacy_room_sensors`, and per-room `privacy_thermal_polygon_mm` for SZ/BZ.

To change anything, edit the corresponding constant near the top of [generate_camera_plan.py](generate_camera_plan.py) and re-run. PNG and JSON regenerate together so they cannot drift apart.

| Want to change…                                  | Edit…                                       |
|--------------------------------------------------|---------------------------------------------|
| A camera's mount point or look direction         | `CAMERAS` list (`x_mm`, `y_mm`, `yaw_deg`)  |
| The K/WZ, Yoga, or Hallway trackable polygon     | `TRACKABLE_AREAS_MM`                        |
| Where the fireplace blocks K/WZ → SZ view        | `FIREPLACE_X_MM` (single int)               |
| The interior strip envelope                      | `INTERIOR_X_MIN_MM` / `..._MAX_MM` etc.     |
| **BZ / SZ thermal fall polygon (floor mm)**      | `PRIVACY_THERMAL_ZONES_MM`                  |
| Fall timing / motion threshold / rest-zone flag| `THERMAL_FALL_DETECTION_EXPORT`, `SZ_REST_ZONE_POLYGON_MM` |
| Privacy-room sensor model / wiring               | `privacy_room_sensors` block in `export_config()` |

---

## 5. What still needs precision from the STEP file

Same list as Phase 1.4, with a few additions (thermal polygons, rest zone, hallway):

1. **Exact pixel-to-mm scale for the rendered floor plan image.** Currently calibrated visually against `floor_plan.png`.
2. **Exact interior wall and structural-frame positions.** The 7 camera mount points and optical trackable polygons are still placed by eye against `floor_plan.png`. **BZ/SZ thermal footprints** in `PRIVACY_THERMAL_ZONES_MM` are **client-locked vertex lists** (exported to JSON); they should still be cross-checked against the STEP-derived plan when that pipeline exists.
3. **Fireplace east-face x.** `FIREPLACE_X_MM = 10000` is a visual estimate.
4. **Exact hallway extents.** The corridor polygon `(12000, 2500) → (17900, 2500) → (17900, 4500) → (12000, 4500)` and cam 7's mount position both need verification against the actual layout.
5. **`sz_rest_zone_polygon_mm` (SZ bed / couch footprint)** defaults to `null` until the installer traces the sleep furniture on the mm plan. Populate `SZ_REST_ZONE_POLYGON_MM` in [generate_camera_plan.py](generate_camera_plan.py) after site survey to tighten suppression of benign lie-down events (see [thermal_fall_detection.py](thermal_fall_detection.py)).

These will be auto-derived properly by `step_pipeline/extract_floor_plan.py` (next phase, uses `cadquery` / `pythonocc` to walk the B-Rep). Until then, Phase 1.5 is good enough to physically install the cameras and start testing.

---

## 6. Open items / Next deliverables

- **Cam 7 / hallway polygon — confirm with client.** Position is a placeholder. Once the client points to the actual structural frame in the corridor, update the four hallway-polygon vertices + cam 7's `(x_mm, y_mm, yaw_deg)` and re-run the script.
- **SZ entrance/exit detection.** Now folded into the AMG8833 thermal sensor in SZ — the heat blob entering the room *is* the entry event. No magnetic door sensor needed. (The mmWave-radar option from Phase 1.4 open-items list is dropped on grounds of empirical failure.)
- **Smoke-test the OEM PoE camera (1 unit) before bulk-ordering.** Spec for the smoke test in [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md) (smoke-test section).
- **Smoke-test the AMG8833 thermal sensor (1 unit) before bulk-ordering.** Walk + lie-down test on a 3 m ceiling; confirm vertical-vs-horizontal blob is unambiguous in the data.
- **`tracking_demo/`** — minimal end-to-end re-ID demo on a single camera (webcam works as a stand-in until the OEM cameras are wired up).
- **`step_pipeline/extract_floor_plan.py`** (next round) — automated STEP → floor_plan.png + room-polygon JSON + scale.
- **`tracking_engine/`** (next round) — full multi-camera service for the Pi 5.
- **`calibration_tool/`** (next round) — browser-based calibration so a non-technical installer can re-map cameras after geometry changes.

---

## 7. Risks (carried forward + new)

- **R1 — Lens FOV may be 66 / 90 / 120 deg.** Verify with one camera before installing all seven (point at a wall 1 m away, measure the visible width). The OEM 2.8 mm M12 lens is closer to 90° than 66°, but the polygon clipping ensures the rendered widget still respects the bound regardless of the actual figure. Update `fov_h_deg` in `CAMERAS` once measured.
- **R2 — Stock OEM firmware phones home.** Mitigated by (a) VLAN with no internet route for cameras + Pi 5, (b) reflashing all 8 boards to OpenIPC after the smoke test passes. See [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md) (camera selection / OpenIPC).
- **R3 — IR LED ring may not have enough range for K/WZ + Yoga.** The OEM boards typically ship with ~5 m range IR LEDs. K/WZ and Yoga are both ≤ 5.7 m on their long axis, so this should be fine, but verify in the smoke test under the client's actual lighting.
- **R4 — ~12-15 fps over PoE.** Tracking engine is being built to tolerate 8-10 fps with motion-prediction recovery between frames, so the actual PoE capacity (typically 25-30 fps for 1080p H.264) is well above the requirement.
- **R5 — Glass walls cause re-ID ghosts.** Detections falling outside the trackable polygon are dropped before they hit the global ID manager.
- **R6 — Fireplace x is an estimate.** `FIREPLACE_X_MM` is currently 10000 mm by visual inspection.
- **R7 — Hallway camera position is a placeholder.** Cam 7's `(x_mm, y_mm, yaw_deg)` and the hallway polygon are guesses. Will be locked once the client confirms the actual structural mounting point and corridor extents.
- **R8 — AMG8833 8×8 might be too coarse for posture classification.** Backup plan is to upgrade to MLX90640 (32×24 thermal) in SZ + BZ at ~ EUR 60 / sensor instead of EUR 25. Decide after the smoke test.
- **R9 — Metal in walls confirmed (not just suspected).** mmWave radar is empirically out as a fallback. If a future requirement needs through-wall presence detection, the only realistic remaining options are PIR sensors (rough) or pressure mats (intrusive); not radar.
