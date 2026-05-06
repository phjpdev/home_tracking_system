# Camera Placement Plan — Phase 1.4 (WiFi prototype, 6 cameras)

**Status:** Camera positions locked by client (installable mounting points). Each per-camera FOV widget is now drawn against an explicit, client-defined trackable polygon, with a fireplace optical blocker between K/WZ and SZ.
**Hardware:** 6 x WiFi cameras already procured by the client (Amazon listing — OV2640-class, ~66 deg horizontal FOV, 640x480 stream).
**Building envelope:** 19.8 m x 10.2 m floor-plan footprint (verified against `ZB Baugruppe TMH2.STEP`), ceiling 3.0 m. Interior rooms occupy a strip y in [4500, 8700] mm; the rest is outdoor terrace, NOT tracked.
**Rooms in scope this round:** K/WZ (full tracking — 4 cameras), Yoga (full tracking — 2 cameras).
**Out of scope this round:** SZ (entry/exit detection deferred — see §6), BZ (bathroom).

---

## What changed since 1.2

Three rounds of client feedback rolled into this version:

1. **Wedges crossed walls (v1.2 → 1.3).** Each FOV wedge is now clipped to its own room's trackable polygon, not the whole interior strip, so a cone never bleeds into a neighbouring room.
2. **Cam 4 too small (v1.2) and untracked north strip in K/WZ (v1.2).** The privacy door watcher is dropped this round; cam 4 becomes a 4th K/WZ tracking camera, and K/WZ goes back to full corner coverage.
3. **Yoga cameras clashed with the SZ↔Yoga wall (v1.2).** Both Yoga cameras moved away from the SZ↔Yoga divider, never face it.
4. **Camera positions hand-tuned by client (v1.4).** Every (x_mm, y_mm, yaw_deg) is now a real installable mount point against the actual structural frame in the building. Marked as `POSITIONS LOCKED BY CLIENT` in `generate_camera_plan.py`; do not change without sign-off.
5. **Trackable widgets redrawn from explicit polygons, not geometric FOV cones (v1.4).** Two polygons (K/WZ L-shape, Yoga rectangle) define the *physical* outer bound of what each room's cameras can possibly see. Per-camera wedges are then clipped to that polygon.
6. **Fireplace optical blocker (v1.4).** The K/WZ trackable polygon's eastern arm stops at the fireplace (x ≈ 10000 mm). K/WZ cameras can see *up to* the fireplace; SZ proper is dark to them.
7. **Output PNG no longer has a side panel (v1.4).** Floor plan fills the whole image (2556 x 1396 px) so it's not cropped in narrow preview panes. The data that used to live in the side panel is now in `cameras_config.json` and this README only.

---

## 1. The 6 placements (Phase 1.4, locked)

| # | Name           | Position (x, y, z) mm | Yaw     | Tilt   | Role                                                       |
|---|----------------|----------------------|---------|--------|------------------------------------------------------------|
| 1 | `cam_kwz_sw`   | (2500, 8600, 3000)   | 330 deg | 35 deg | K/WZ tracking — south-west, looks NE-ish                   |
| 2 | `cam_kwz_nw`   | (2500, 5200, 3000)   |  25 deg | 35 deg | K/WZ tracking — north-west, looks SE-ish                   |
| 3 | `cam_kwz_ne`   | (6200, 4900, 3000)   | 155 deg | 35 deg | K/WZ tracking — north-east on K/WZ-BZ frame, looks SW into K/WZ + east toward fireplace |
| 4 | `cam_kwz_se`   | (6300, 6400, 3000)   | 330 deg | 35 deg | K/WZ tracking — middle-east on K/WZ-BZ frame, looks NE toward fireplace |
| 5 | `cam_yoga_ne`  | (17800, 4900, 3000)  | 155 deg | 35 deg | Yoga tracking — north-east, looks SW across Yoga           |
| 6 | `cam_yoga_se`  | (14200, 8200, 3000)  | 330 deg | 35 deg | Yoga tracking — south on SZ-Yoga frame, looks NE; stereo with #5 |

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

Each room has an explicit polygon describing what its cameras can physically see. The polygons are defined in `TRACKABLE_AREAS_MM` near the top of [generate_camera_plan.py](generate_camera_plan.py); per-camera FOV wedges are clipped to them so:
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

- Slightly inset from the room walls so the polygon represents physically reachable floor (people don't actually stand pressed against a wall).
- Cam 5 sits at the NE corner and fans SW; cam 6 sits at the south-on-SZ-Yoga-frame and fans NE — together they cover the rectangle stereoscopically.

---

## 3. Why this layout

**K/WZ — 4 cameras**

K/WZ is 5.0 m wide x 3.9 m deep. The 4 cameras are split across the SW + NW corners and along the K/WZ-BZ shared frame:
- 2-camera coverage of every point in the K/WZ rectangle (re-ID handover always possible).
- The previously-missed N strip is now covered by cams 2 + 3 (they sit on / near the N wall).
- Cams 3 + 4 (mounted on the BZ-side structural frame, the only solid wall in that area) extend coverage east into the upper BZ strip up to the fireplace, so the kitchen-island side is well covered.

**Yoga — 2 cameras**

Yoga is 5.7 m wide x 3.9 m deep. Mounting both cameras on the east side / SZ-Yoga frame and aiming them diagonally back gives:
- Stereo coverage of the centre and east half of Yoga (the actual usable space).
- Wedges that physically face *away* from the SZ↔Yoga wall, so they never look through it.
- With per-room polygon clipping, the cones are bounded by Yoga's rectangle. They cannot visually intrude on SZ even if their geometric extent would.

**Mounting points**

The long N + S walls of the building are continuous sliding glass. The only solid wall fixing point on the room perimeter is the **structural steel frame between glass panels at every interior corner / room divider**. All 6 cameras mount on a ceiling drop bracket bolted to one of those frames. The exact (x_mm, y_mm) coordinates above are picked to land on those specific frames.

### Why no SZ camera this round

The two ways to do SZ in this prototype were:
- A near-top-down door watcher (v1.2's `cam_sz_door`, tilt 80°). Client said: too small an area.
- A regular tracking camera in SZ. Conflicts with the privacy requirement (no per-person coords inside the bedroom).

Rather than ship a half-broken SZ solution, it's deferred. SZ entrance/exit detection is added back as soon as we agree on the approach — see §6 for the candidate replacements.

---

## 4. How to run / regenerate the plan

```bash
pip install matplotlib pillow numpy
python generate_camera_plan.py
```

Outputs (to `output/`):
- `camera_placement_plan.png` — the visual the client signs off on (full-width floor plan, no side panel).
- `cameras_config.json` — machine-readable camera config the tracking engine reads at startup. Includes per-room `trackable_polygon_mm` so the runtime engine can drop detections that fall outside it.

To change anything, edit the corresponding constant near the top of [generate_camera_plan.py](generate_camera_plan.py) and re-run. PNG and JSON regenerate together so they cannot drift apart.

| Want to change…                              | Edit…                                       |
|----------------------------------------------|---------------------------------------------|
| A camera's mount point or look direction     | `CAMERAS` list (`x_mm`, `y_mm`, `yaw_deg`)  |
| The K/WZ or Yoga trackable polygon shape     | `TRACKABLE_AREAS_MM`                        |
| Where the fireplace blocks K/WZ → SZ view    | `FIREPLACE_X_MM` (single int)               |
| The interior strip envelope                  | `INTERIOR_X_MIN_MM` / `..._MAX_MM` etc.     |

---

## 5. What still needs precision from the STEP file

The STEP file (`ZB Baugruppe TMH2.STEP`, SolidWorks 2019, AP203) confirms the 19.8 x 10.2 m envelope but is too noisy to extract exact wall geometry with simple regex (CARTESIAN_POINTs include construction geometry, normals and reference points outside the building). These values are still visual estimates and will be auto-derived properly by `step_pipeline/extract_floor_plan.py` (next phase, uses `cadquery` / `pythonocc` to walk the B-Rep):

1. **Exact pixel-to-mm scale for the rendered floor plan image.** Currently calibrated visually against `floor_plan.png`.
2. **Exact interior wall and structural-frame positions.** Today, the 6 camera mount points and both polygons are placed by eye against `floor_plan.png`. If the structural frames are offset from the visual estimate by a few hundred mm, each `(x_mm, y_mm)` can be shifted by that amount; nothing else changes.
3. **Fireplace east-face x.** `FIREPLACE_X_MM = 10000` is a visual estimate; the STEP geometry should give us this within a millimetre.

Until that automation is in place, the Phase 1.4 placements are good enough to physically install the cameras and start testing.

---

## 6. Open items / Next deliverables

- **SZ entrance/exit detection.** Three options on the table:
   - **(a)** Add a 7th camera at the K/WZ↔SZ doorway (corridor between K/WZ and SZ along the north of the BZ block), tilt ~70°, framed only on the threshold so it cannot see the bed.
   - **(b)** Magnetic door sensor (~€5) on the SZ door + the existing K/WZ cameras to infer "person crossed into SZ". Cheapest, no extra camera, but only event-based, no continuous presence.
   - **(c)** mmWave radar (~€30, e.g. Seeed XIAO ESP32C6 + LD2410) for presence-only inside SZ. No image data ever leaves the bedroom — ideal for the privacy story.
   
   Recommendation: (b) for now (cheapest, ships fastest) and (c) as a phase-2 upgrade. Awaiting client decision.
- **`tracking_demo/`** — minimal end-to-end re-ID demo on a single camera (webcam works as a stand-in until the WiFi cameras are wired up). Lets the client *see* the tracking concept without waiting for the full system.
- **`docs/FINAL_CAMERA_SELECTION.md`** — the production PoE camera recommendation: small, cheap, IR-night, 24/7-streaming-reliable.
- **`step_pipeline/extract_floor_plan.py`** (next round) — automated STEP → floor_plan.png + room-polygon JSON + scale, so any geometry change rebuilds the placement plan in one command.
- **`tracking_engine/`** (next round) — full multi-camera service for the Pi 5.
- **`calibration_tool/`** (next round) — browser-based calibration so a non-technical installer can re-map cameras after geometry changes.

---

## 7. Risks (carried forward)

- **R1 — Lens FOV may be 66 / 120 / 160 deg.** Verify with one camera before installing all six (point at a wall 1 m away, measure the visible width). If the actual horizontal FOV differs, update `fov_h_deg` in `CAMERAS`; the polygon clipping ensures the rendered widget still respects the bound regardless.
- **R2 — WiFi reliability.** Dedicated 2.4 GHz SSID for cameras only; static IPs by MAC; powered from wall sockets, not USB hubs.
- **R3 — No IR night vision.** These prototype cameras are RGB only. The production system requires PoE cameras with built-in IR for "music follows me at night" to work. See [docs/FINAL_CAMERA_SELECTION.md](docs/FINAL_CAMERA_SELECTION.md) for the recommended replacement model.
- **R4 — ~10-15 fps over WiFi, not 30.** Tracking engine is being built to tolerate 8-10 fps with motion-prediction recovery between frames.
- **R5 — Glass walls cause re-ID ghosts.** Detections falling outside the trackable polygon are dropped before they hit the global ID manager.
- **R6 — Fireplace x is an estimate.** `FIREPLACE_X_MM` is currently 10000 mm by visual inspection; if the real value is materially different the K/WZ polygon will need its arm extended or shortened. One-line change.
