# Camera Placement Plan — Phase 1 (WiFi Prototype)

**Status:** Proposal for client review
**Hardware:** 5 × ESP32-CAM-MB (OV2640 sensor) over WiFi
**Building:** ~17.5 m × 5 m interior, ceiling height 3.0 m
**Rooms covered:** K/WZ (kitchen + living), BZ (bathroom), SZ (bedroom + hallway), Yoga

---

## 1. What this deliverable contains

```
camera_placement_v1/
├── floor_plan.png                  ← source 2D floor plan (input)
├── generate_camera_plan.py         ← the script that produces the outputs
├── README.md                       ← this document
└── output/
    ├── camera_placement_plan.png   ← annotated plan for review (image)
    └── cameras_config.json         ← machine-readable config for the
                                      tracking engine
```

You only need to look at two things to review the proposal:

1. **`output/camera_placement_plan.png`** — visual overview of where every camera goes, where it points, and what it covers.
2. **This README** — explains *why* each camera is where it is, what trade-offs were made, and what the risks are.

If you want to change a placement, edit the `CAMERAS` list in `generate_camera_plan.py` and re-run — the image and JSON regenerate together so they cannot drift out of sync.

---

## 2. How to run the script

### Requirements

- Python 3.10 or newer
- Three libraries:

```bash
pip install matplotlib pillow numpy
```

### Run

From the `camera_placement_v1/` folder:

```bash
python3 generate_camera_plan.py
```

It will print:

```
[ok] wrote .../output/camera_placement_plan.png
[ok] wrote .../output/cameras_config.json
Done.
```

### Editing placements

All five cameras are defined in one place near the top of the script — the `CAMERAS` list. Each entry has the fields:

| Field        | Meaning                                                           |
|--------------|-------------------------------------------------------------------|
| `id`         | Stable integer ID (1..5). Used to identify the camera everywhere. |
| `name`       | Stable string name (e.g. `cam_kwz_ne`). Used by the tracking engine. |
| `x_mm, y_mm` | Floor position in millimetres from the building's NW corner.      |
| `z_mm`       | Mounting height above the floor (millimetres).                    |
| `yaw_deg`    | Compass-like horizontal direction the camera faces (see §4).      |
| `tilt_deg`   | How many degrees below horizontal the camera points (+ = down).   |
| `fov_h_deg`  | Camera horizontal field-of-view in degrees (66 for the stock OV2640 lens). |
| `room`       | Which room the camera serves.                                     |
| `role`       | What the camera does in the system (tracking vs. presence-only).  |

Change values, save, re-run.

---

## 3. Coordinate system (important for the installer)

```
  Origin (0, 0) is the NW (top-left) corner of the building footprint.

       NW (0, 0) ────────────────────────────►  +x  (east)
            │
            │
            ▼
        +y  (south)
            │
            │
       SW (0, 5000)

  +z is "up" — toward the ceiling.
```

- **Units are millimetres throughout.** No mixed mm/cm/m.
- All x, y values reference the **inside** corner of the building, not the outer envelope.
- The interior rectangle is **17 500 mm wide × 5 000 mm deep**.
- The full envelope is the 19.75 m × 10.20 m rounded shape on the plan; the rest of that envelope is the outdoor deck/garden, not part of the tracked space.

### Yaw (which direction the camera looks)

```
                  270° (north)
                       ▲
                       │
   180° (west) ◄───────┼───────► 0° (east)
                       │
                       ▼
                   90° (south)
```

### Tilt

`tilt_deg` is **the angle below horizontal**.
- `0°` = camera looks perfectly horizontal (would see the wall opposite, not the floor — useless).
- `90°` = camera looks straight down (sees the floor directly under itself only).
- **Recommended for tracking:** 30–40°. The ceiling-mounted view is wide enough to track people without too much grazing-angle distortion.
- **Recommended for door watchers:** 55–65°. We want the camera to see the doorway and only the doorway, nothing deeper into the bedroom or bathroom.

---

## 4. Why these 5 placements? (the reasoning the client should review)

### Big picture: why 5 cameras for 4 rooms

We use **5 cameras** because **K/WZ has 2 of them** — one in the NE corner and one in the SW corner.

This is not about coverage (one wide-angle camera could cover most of K/WZ). It's about **re-identification**. The whole point of this system is that the music and lights follow the person — meaning a person walking from the kitchen across to the sofa **must keep the same global ID**. With only one camera, when someone walks behind the kitchen island, the tracker briefly loses them, and when they re-emerge there's no way to know it's the same person without an embedding comparison from a *different angle*. Two overlapping cameras give the tracker independent views of the same person at the same moment, which is what makes the re-identification reliable.

The other three rooms are smaller and have tighter constraints (privacy in BZ/SZ, single-room scope in Yoga), so one camera each is enough.

### Camera-by-camera reasoning

**Cam 1 — K/WZ NE corner (`cam_kwz_ne`)**
- Position: top-right corner of the kitchen+living area, just inside the wall.
- Mounted on the ceiling at 3 m, tilted 35° down, facing SW into the room.
- Covers: kitchen island, dining table, half of the sofa area.
- Why here: the NE corner has a direct line of sight to both the kitchen working area and the dining zone — these are the two highest-occupancy spots in the room. The 35° tilt at 3 m height produces a floor footprint centred ~4.3 m away, which lands right on the dining table.

**Cam 2 — K/WZ SW corner (`cam_kwz_sw`)**
- Position: bottom-left corner of the kitchen+living area, just inside the wall.
- Mounted on the ceiling at 3 m, tilted 35° down, facing NE into the room.
- Covers: sofa area, far side of the kitchen island, the entry door from the deck.
- Why here: the SW position sees the parts of K/WZ that Cam 1 cannot — primarily the south side of the kitchen island (which would otherwise be a blind spot directly under Cam 1) and the entry from the outdoor deck. Critically, **Cam 1 and Cam 2's coverage overlaps over the central sofa area**, which is the re-ID handover zone. A person walking from kitchen to sofa is seen by both cameras simultaneously, so the system has independent embeddings to confirm "still the same person".

**Cam 3 — BZ (`cam_bz_door`) — PRESENCE-ONLY**
- Position: just inside the bathroom, near the door.
- Mounted on the ceiling at 3 m, tilted **60°** (steep), facing back toward the door.
- Covers: only the doorway and the small area immediately inside it.
- Why here: the spec is explicit that the bathroom is a **privacy zone** — we are not allowed to know *where* in the bathroom a person is, only *that someone is in there*. The steep 60° tilt means the camera literally can't see deeper than ~1.7 m into the room, which is just past the door. Combined with the software-side privacy filter (the tracking engine outputs only `zone: "bathroom", privacy: true`, never coordinates), we have both physical and software safeguards.
- **Decision point for the client:** if even doorway-watching from inside is too much, we can replace Cam 3 with a magnetic door sensor and a PIR motion sensor. That gives you presence detection with zero camera in the room. Tell us if you want this swap.

**Cam 4 — SZ (`cam_sz_door`) — PRESENCE-ONLY**
- Position: just inside the bedroom, near the door from K/WZ.
- Mounted on the ceiling at 3 m, tilted 55° down, facing back toward the door.
- Covers: only the bedroom doorway and a small area just inside.
- Why here: the spec said *"the bedroom we only need to see the entrance and exit"*. This camera literally watches the doorway — when someone walks in, we increment the bedroom-occupancy count and link their ID to the room. When someone walks out (we see their face/embedding crossing the door from inside to outside), we decrement.
- The steep tilt of 55° is a deliberate privacy guard: the camera physically cannot see the bed area.

**Cam 5 — Yoga NW corner (`cam_yoga`)**
- Position: top-left corner of the yoga room, just inside the wall.
- Mounted on the ceiling at 3 m, tilted 35° down, facing SE into the room.
- Covers: most of the yoga room.
- Why here: yoga room is small enough (~5 m × 4.5 m) that one corner camera with a 66° lens covers it adequately. We only need approximate position here (the music-follows-person logic for this zone is binary "is anyone in the yoga room" plus rough position to know if they're near the speakers).

### What's deliberately **not** covered

- **The outdoor deck/garden** — out of scope for Phase 1. If you want music-follows-you on the deck, that's Phase 2 (waterproof cameras, different problem).
- **The K/WZ↔SZ corridor blind spot** — the small wall jog where K/WZ and BZ meet creates a tiny dead zone (~1 m × 1 m) right at the doorway between K/WZ and SZ. People only spend ~1 second crossing it; the global ID manager handles this with motion prediction (more on this in the engine docs).
- **Inside the bathroom** — by design (privacy).
- **Inside the bedroom proper** — by design (privacy).

---

## 5. Risks and trade-offs (please read before signing off)

These are the realistic things that can go wrong with this exact hardware. None are showstoppers; all are mitigations to know about up front.

### R1. The OV2640 default lens is ~66° horizontal — narrower than ideal

The Amazon listing for the ESP32-CAM-MB does not specify the lens's field of view, and these boards ship with whatever lens the factory had. Most ship with the **standard 66° lens**, which is what the placement plan assumes. Some ship with **120° wide-angle** or **160° fisheye**.

If your cameras turn out to have a wider lens than 66°, that's actually good news — coverage increases, fewer cameras potentially needed in K/WZ. But the placement angles will need to be re-tuned, and a fisheye lens needs an extra calibration step (un-distortion).

**Mitigation, on day one:**
1. Plug in one camera and stream its image to your laptop.
2. Hold the camera 1 metre from a flat wall, perpendicular to it.
3. Measure how wide the visible area on the wall is.
   - ~1.3 m wide → 66° lens (proceed with this plan as-is)
   - ~3.4 m wide → 120° lens (re-run the script with `fov_h_deg: 120` in each entry)
   - ~11 m wide → 160° fisheye (special handling needed; let us know)

### R2. ESP32-CAM WiFi is famously flaky

ESP32-CAMs have known issues with WiFi reconnection, dropped frames, and crashes when sharing a network with many other devices. For 5 cameras streaming 640×480 at 12 fps, total bandwidth is ~5–8 Mbps. That is well within any modern router's capacity, but the *reliability* of the cameras themselves is the constraint.

**Mitigations:**
- **Dedicated 2.4 GHz SSID for cameras only.** No phones, laptops, IoT junk on this SSID. Set the 2.4 GHz band to a fixed channel (1, 6, or 11) away from neighbouring networks.
- **Static IP per camera**, assigned by the router by MAC address. The Pi is configured to talk to specific IPs; if cameras roam DHCP addresses we lose them.
- **Power them via the included USB-C adapter** (5V 1A). Do not use USB hubs, do not power them from the Pi's USB. ESP32-CAMs brown-out and reboot under voltage sag, looking like WiFi disconnects.
- **One AP, line-of-sight where possible.** A single AP in the K/WZ ceiling reaches all 5 cameras; mesh adds latency.

If, after these mitigations, you still see frequent drops, the next step is to swap to **PoE Ethernet cameras** (Reolink RLC-510A or similar, ~€60 each). The tracking engine treats RTSP-over-Ethernet identically to RTSP-over-WiFi; only the URLs change. This is Phase 2.

### R3. No usable night vision

The OV2640 has an internal IR-cut filter, and the ESP32-CAM-MB has no IR illuminator. This means **the system only works under normal room lighting**. After dark, in unlit rooms, the cameras see nothing. There is no way to fix this with this hardware.

**Mitigations:**
- For Phase 1 (prototype), this is acceptable — you're validating the tracking logic, not running 24/7.
- For Phase 2, the bedroom watcher (Cam 4) would need to be replaced with a PIR + door sensor so bedroom presence still works at night. Other rooms either need lighting (which they'd have anyway when occupied) or PoE cameras with real IR illuminators.

### R4. Frame rate is realistic ~10–15 fps, not 30 fps

The ESP32-CAM tops out at around 12–15 fps streaming JPEG at 640×480 over WiFi. Below ~10 fps, fast-walking persons cause tracker breaks (a person moves >0.5 m between frames, which is more than ByteTrack's IoU threshold can match). This means the latency target of 150 ms might be hard to meet if WiFi latency adds another 50–80 ms.

**Mitigation:**
- Track at 8–10 fps but expect a few false breaks per minute when people are moving fast. The global ID manager's spatial-gating logic is designed to recover from these.
- Make the Pi's WiFi link to the AP wired (Ethernet from Pi to router), so the only wireless hop is camera→AP. Halves the round-trip.

### R5. Glass walls reflect IR / cause ghosts

You mentioned in earlier discussion that there's significant glass in the K/WZ walls. Cameras pointed across glass can pick up reflections of objects on the other side — this manifests as duplicate "ghost" persons on the floor plan.

**Mitigation:**
- The tracking engine filters detections that fall outside the room polygons before reporting them. So even if a ghost detection appears, it gets dropped if it's outside the K/WZ polygon.
- If reflections become a real problem, we can angle Cam 1 and Cam 2 slightly down by a few more degrees (40° tilt instead of 35°) so they look more at the floor and less across glass.

### R6. K/WZ is the only room with two cameras

If one camera in K/WZ fails, we lose stereo overlap and re-ID can drift in K/WZ. Other rooms with one camera each don't have this redundancy at all.

**Mitigation:**
- Tracking engine raises a "camera offline" alert immediately, so the user knows about the degradation.
- Single-camera rooms still work for basic tracking — they just lose re-identification reliability when a person crosses behind furniture. This is acceptable for BZ/SZ (presence-only anyway) and for Yoga (small room with no real obstructions).

---

## 6. What the client needs to confirm before installation

Please confirm or correct these assumptions:

1. **Ceiling height is 3 000 mm in all four rooms.** If different per room (e.g., yoga room is taller), tell us and we'll set per-camera `z_mm`.
2. **There is a clean ceiling mounting surface in each corner where we want cameras.** Specifically: K/WZ NE, K/WZ SW, BZ near door, SZ near door, Yoga NW. If any of these locations has a beam, vent, or other obstruction, we'll move the camera by ±300 mm.
3. **Power is available within ~2 m of each camera location** (USB-C from a wall socket). The ESP32-CAM-MB does not run on PoE.
4. **The OV2640 lens is ~66° as standard.** (See R1 mitigation — measure with one camera before installing all five.)
5. **The bathroom and bedroom strict privacy requirement** is to never log per-person coordinates inside those rooms — only zone occupancy. Cam 3 and Cam 4 sit just inside the door at a steep tilt to enforce this physically. **Confirm this is acceptable, or tell us if you'd prefer no camera at all in those rooms (we'd swap to PIR + door sensor).**

---

## 7. What comes next

Once placements are signed off:

1. **Install** the 5 ESP32-CAM-MBs at the specified positions and angles. Power them up, confirm WiFi connection.
2. **Run the calibration tool** (separate deliverable) once per camera. This computes the homography from each camera's pixel coordinates to floor-plan mm coordinates. Required because mounting will inevitably differ from the plan by a few cm and a few degrees.
3. **Start the tracking engine** on the Pi. It reads `cameras_config.json`, opens RTSP streams, runs detection + ByteTrack + re-ID, and POSTs person positions to the Maro server.
4. **Validate end-to-end:** walk between rooms, watch a dot move across the floor plan in real-time on the dashboard.

If anything in this proposal needs to change, edit `generate_camera_plan.py`, re-run, and review the new image.
