# Final Camera Selection — Production Hardware Recommendation

**Why this doc exists:** the prototype phase (currently 1.4, see top-level
[README.md](../README.md)) uses 6 WiFi cameras the client already ordered.
They have **no IR night vision** and WiFi is known flaky. The production
system needs cameras that satisfy the full job spec — "PoE, RGB day + IR
night, 24/7, cheap, small enough to hide".

This doc is the buy-list for that production hardware. It is meant to be
read AFTER the WiFi prototype is up and re-ID is validated end-to-end —
i.e. the camera selection only matters once we know the *software* works.

The 6 prototype mount points are the same physical positions the
production cameras will use; only the camera bodies are swapped. So the
production order should be **6 cameras for the locked positions + 1-2
spares** (see §"Supporting hardware to buy" below).

## Hard requirements (from the spec)

1. **PoE 802.3af** powered (single Ethernet cable per camera, no separate PSU)
2. **RTSP H.264 stream** (Pi 5 has hardware H.264 decode; H.265 also works
   but uses slightly more CPU on Pi)
3. **RGB day + IR night** with mechanical IR-cut filter (true night vision,
   not just low-light)
4. **24/7 streaming reliable** — runs for months without crashing
5. **Hideable in a 3 m ceiling** — small footprint, ideally dome or
   pinhole form factor
6. **Cheap-ish** — sub-EUR 100 per camera (target sub-EUR 70)
7. **Local-only operation** — no mandatory cloud account, no firmware
   that phones home

## Soft requirements (nice-to-have)

- ONVIF Profile S (so the calibration tool can auto-discover cameras)
- Dual-stream output (main + sub) so the tracking engine can pull a
  low-res sub-stream and save bandwidth + decode CPU
- Built-in motion detection / person detection as fallback
- WDR / HDR for the K/WZ cameras (sliding glass doors create
  high-contrast scenes the standard sensor blows out)

## Top recommendation: Reolink RLC-520A (4 MP PoE Dome)

- **Price:** ~EUR 65 / camera in DE (Reolink direct or Amazon)
- **Form factor:** 95 mm dome, ceiling-mountable, recessed look
- **Sensor:** 4 MP, 1/3" CMOS, 80 deg horizontal FOV
- **Stream:** RTSP H.264 / H.265, dual-stream
  - Main: 2560x1440 @ up to 25 fps
  - Sub: 640x480 @ up to 15 fps (the one we actually use)
- **IR:** 18 IR LEDs, ~30 m range (overkill, but cuts in/out cleanly)
- **PoE:** 802.3af, ~5 W
- **Codec note:** firmware 3.1+ has H.265 main + H.264 sub, which is the
  best combo: H.264 sub for tracking (cheap to decode), H.265 main for
  optional recording (smaller files)
- **Local-only:** Reolink works without an account; the cloud features
  are opt-in. The web UI has a "disable Reolink Cloud" toggle that we
  flip during install.
- **RTSP URL pattern:**
  ```
  rtsp://admin:PASSWORD@<ip>:554/h264Preview_01_sub   # tracking
  rtsp://admin:PASSWORD@<ip>:554/h264Preview_01_main  # recording
  ```
- **Why this over the bullet RLC-510A:** the dome is 95 mm vs. the
  bullet's 165 mm. Same internals, less visual clutter on the ceiling.

## Backup option: Hikvision DS-2CD1143G2-I (4 MP PoE Dome)

- **Price:** ~EUR 90 / camera in DE
- Same form factor, same FOV
- Better build quality (Hikvision OEM)
- True ONVIF Profile S support out of the box (Reolink's ONVIF is buggy
  in some firmware versions; we work around with raw RTSP URLs)
- Recommended if Reolink turns out unreliable in field testing

## Maximum-hide option: pinhole "covert" PoE camera

If the client wants the cameras *invisible* (no visible dome at all):

- **Hikvision DS-2CD2D14WD** — 30 mm pinhole, mounts through a
  small hole in the ceiling, only the lens is visible (~5 mm)
- **Price:** ~EUR 110-130
- **Caveat:** these typically don't have IR (the lens is too small),
  so they only work in a lit room. For night-vision rooms (bedroom door
  watcher) we still need a regular camera.

## What NOT to buy

- **Tapo / TP-Link cloud cameras** — require a cloud account, hard to
  disable, RTSP support is firmware-version-dependent. Avoid.
- **Ring / Nest / Arlo** — fully cloud-locked. Out of scope for an
  offline system.
- **ESP32-CAM-MB with PoE shield** — DIY route. The PoE shields exist,
  but you end up with a kludge that has the same WiFi-firmware
  reliability issues as the standalone ESP32-CAM, plus a fragile
  shield connector. Not worth saving EUR 40 / camera.
- **Anything 8 MP / 4K** — Pi 5 H.264 decode is plenty for 6 streams
  at 1080p; 4K just doubles the decode cost for no tracking benefit
  (the YOLO model crops to 640x384 anyway).

## Supporting hardware to buy alongside the cameras

| Item                                            | Qty | Approx EUR / unit  |
|-------------------------------------------------|-----|--------------------|
| Reolink RLC-520A (camera)                       | 8   | 65                 |
| TP-Link TL-SG108PE (8-port PoE+ managed switch) | 1   | 60                 |
| Cat6 cable, 305 m / 1000 ft box                 | 1   | 70                 |
| RJ45 ends + crimping tool (if not already)      | 1   | 25                 |
| Pi 5 8 GB                                       | 1   | 80                 |
| Hailo-8L M.2 HAT                                | 1   | 70                 |
| Active SSD (storage for recordings, optional)   | 1   | 30                 |
|                                                 |     | **~EUR 800 total** |

(Quantity of cameras = 8 = 6 production replacements for the locked
prototype positions + 2 spares for either the deferred SZ entry/exit
camera (see top-level README §6) or future Phase 2 additions like deck
tracking.)

## Rough decision flow for ordering

1. **Wait until the prototype works.** No camera order until the WiFi
   prototype proves the tracking + re-ID logic is solid. We do not want
   to buy 8 production cameras and discover the latency budget is wrong.
2. Once the prototype works, **buy 1 RLC-520A first.** Validate:
   - Does it stream stably to the Pi for 48 h without dropping?
   - Can we decode 1 main + 1 sub stream with the Pi 5 H.264 hw decoder?
   - Does the IR cut-in/out under our actual lighting conditions?
3. Only after that smoke-test, **buy the remaining 7 + the switch + cable**.
4. **Do NOT mix camera models.** Pick one model, buy all of one batch
   from the same supplier. Mixed models = mixed firmwares = endless
   per-camera quirks in the tracking engine.

## Power & cabling check (for 30-35 m runs the client mentioned)

- 802.3af spec: 100 m max cable run, ~12.95 W deliverable at far end.
- The RLC-520A draws ~5 W. Plenty of headroom on a 35 m run.
- **No PoE extender / amplifier needed** for these distances. Only
  if a single run goes >100 m would we add an inline PoE injector.
- Cat6 (not Cat5e) — small price difference, more headroom for the
  occasional 4K stream if we ever need it.

## Privacy / firewall notes

- Cameras and Pi 5 should be on a **separate VLAN with no internet
  access**. Block outbound traffic at the router so the cameras
  cannot phone home to Reolink even if a future firmware tries to.
- Pi 5 has internet only when the operator SSHes in and explicitly
  enables it (for `apt update`).
- The Maro server can be on the regular LAN (it serves the dashboard
  to the user's phone).
