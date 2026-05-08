# Final Hardware Selection — Production Cameras + Privacy-Room Sensors

This doc is the definitive buy list for the production deployment.
Phase: 1.5 (7 cameras + non-camera fall-detection sensors in SZ + BZ).

For the camera placement geometry, see `generate_camera_plan.py`
and `output/cameras_config.json`. For the system overview, see the
top-level [README.md](../README.md).

---

## 1. Camera selection — APPROVED

**Status:** Approved for deployment BOM. Order reference placed per project procurement.

### What was approved

| Item                | Detail                                                                |
|---------------------|-----------------------------------------------------------------------|
| Type                | OEM "DIY IP/PoE Camera Module Board"                                  |
| Source              | AliExpress (seller "Shop1318225 Store") — ship-from-EU option         |
| SoC                 | HiSilicon Hi3516-class                                                |
| Sensor              | Sony IMX335 (or 1/2.7" equivalent)                                    |
| Resolution          | up to 5 MP (we run 1080p sub-stream for tracking)                     |
| Codec               | H.264 main + H.265 main, dual-stream                                  |
| Lens                | M12 2.8 mm fixed (≈ 90° H-FOV) — matches the 66° figure used in the   |
|                     | placement script with margin for cropping                             |
| IR                  | Built-in IR LED ring + mechanical IRCUT filter (true day/night)       |
| PoE                 | 802.3af on board (no separate splitter needed) — `5MP POE (48V)` SKU  |
| Protocol            | ONVIF + native RTSP (`/stream0`, `/stream1`)                          |
| Form factor         | Bare PCB ~38 × 38 mm + lens — small enough to recess into a ceiling   |
|                     | frame so only the lens objective is visible from the room             |
| Unit price          | ~ EUR 36 / board (includes RJ45 PoE pigtail)                          |
| Quantity            | **8 units** = 7 placements (cams 1–7) + 1 spare                       |

### Why this and not a finished consumer camera

Consumer boxed cameras (e.g. Reolink RLC-520A, Hikvision ColorVu, Axis P1245) were ruled out on **cost** for this deployment; the OEM board hits PoE, RTSP, RGB+IR with mechanical IRCUT, 1080p+, and no mandatory cloud at ~ EUR 36 / unit including shipping.

The trade-off is no manufacturer warranty and Chinese stock firmware
that wants to phone home. Both are addressed below.

### Stock-firmware risk → OpenIPC migration

These OEM boards ship with a generic Chinese firmware that:

- Has well-documented hard-coded outbound URLs (cloud, telemetry).
- Sometimes embeds a Telnet root account with a default password.
- Drifts in behaviour between batches.

**Mitigation, in order:**

1. **Network-level isolation first.** All cameras + the Pi 5 sit on a
   separate VLAN with no internet route. Block outbound at the router.
   The cameras *cannot* phone home even if the firmware tries.
2. **OpenIPC firmware second.** Once the smoke test passes (see the smoke-test section below),
   reflash all 8 boards to OpenIPC ([openipc.org](https://openipc.org)).
   OpenIPC is open source, supports the Hi3516 SoC + IMX335 sensor
   combo natively, and gives us:
   - Reproducible firmware across all 8 cameras
   - Native ONVIF + RTSP, no telemetry
   - SSH instead of Telnet
   - Reliable 24/7 streaming (its main use case is exactly this)
3. **One model, one batch, one supplier.** Mixed firmwares = endless
   per-camera quirks in the tracking engine. Same SKU, same lens,
   same shipment.

### RTSP URL pattern (post-flash)

After OpenIPC, every camera exposes:

```
rtsp://<ip>:554/stream0   # main, 1080p H.264 — optional recording
rtsp://<ip>:554/stream1   # sub,   640x480 H.264 @ 15 fps — tracking
```

`cameras_config.json` will be regenerated with each unit's IP when
the calibration tool walks the network.

---

## 2. Privacy-room sensors (SZ + BZ) — fall detection

The privacy rooms cannot have a camera at all. The original plan was
24/60 GHz mmWave radar (Aqara FP2, Apollo R1). **On-site testing of
both units failed.** Cause: the building has metal in the
walls (frame structure + aluminium glass mullions), which is exactly
the failure mode mmWave is most sensitive to — multipath reflections
scramble the radar return.

### Replacement: low-resolution thermal IR sensor

**Why this works where mmWave doesn't:**

| Property                  | mmWave (24/60 GHz) | Low-res thermal IR |
|---------------------------|--------------------|--------------------|
| Affected by metal walls   | Yes (severely)     | No (it's optical)  |
| Privacy concerns          | None (no image)    | None (8×8 pixels — no facial features, no clothing detail) |
| Detects fall posture      | Indirect (motion)  | Direct (heat blob is horizontal vs. vertical) |
| Works in total darkness   | Yes                | Yes (it sees heat, not light) |
| Through-blanket detection | Yes                | Partial (warmer head/limb spots stay visible) |
| Off-the-shelf cost        | ~ EUR 70           | ~ EUR 30           |
| ESPHome / open-source     | Limited            | Native support     |

### What was approved

| Room | Sensor                                                  | Approx EUR | Purpose                                    |
|------|---------------------------------------------------------|-----------:|--------------------------------------------|
| SZ   | Panasonic AMG8833 (Grid-EYE) 8×8 thermal + ESP32        |        25  | Presence + fall posture                    |
| BZ   | Panasonic AMG8833 + ESP32                               |        25  | Presence + fall posture                    |
| BZ   | Aqara water-leak sensor (Zigbee)                        |        15  | Wet-floor secondary check (fall after slip)|

`MLX90640` (32×24 thermal) is a higher-resolution alternative at
~ EUR 60–80 if 8×8 turns out too coarse for posture classification.
Recommend starting with AMG8833 and upgrading only if needed.

**Floor footprints for fall logic (vertex list in mm, exported as `privacy_thermal_zones_mm`):**

- **BZ:** `(6400, 7400) → (10000, 7400) → (10000, 8700) → (8500, 8700) → (8500, 9200) → (6400, 9200)`
- **SZ:** `(10100, 5000) → (14200, 5000) → (14200, 8000) → (10100, 8000)`

### Fall logic (deployed) — SZ vs BZ

All logic uses the **floor-plan envelope frame** (mm, +x east, +y south). The **blob centroid** projected to the floor must fall inside the closed polygon `privacy_thermal_zones_mm["SZ"]` or `["BZ"]` in `output/cameras_config.json` (regenerated from `PRIVACY_THERMAL_ZONES_MM` in `generate_camera_plan.py`). Upstream steps (rolling background, centroid, blob orientation from PCA / ellipse) stay the same regardless of 8×8 or 32×24 hardware.

**SZ (bedroom) — fall vs going to bed**

1. **Trajectory gate (pre-stillness).** On the edge **vertical → horizontal**, measure  
   `Δt = t_horizontal − t_vertical_entry`.  
   - `Δt ≤ rapid_transition_max_s` (default **1.0 s**) → **rapid** fall cue (high confidence track if the rest of the chain fires).  
   - `Δt ≥ slow_transition_min_s` (default **3.0 s**) → deliberate **lie-down** cue; combined with `sz_suppress_if_slow_to_rest_zone` this **suppresses** a fall alarm after confirmation (going to bed).
2. **Confirmation (30 s, no motion).** The alarm does **not** fire on the first horizontal frame. Require **continuous** horizontal posture, **on floor**, inside the SZ polygon, with `motion_normalized ≤ motion_threshold_normalized` (default **0.02**) for **`sz_still_confirmation_s` (30 s)**. Any movement above the threshold **resets** this timer (e.g. sitting on the floor stretching — the person moves and no event fires; a true fall stays still).
3. **Rest zone (optional).** `sz_rest_zone_polygon_mm` defaults to **null**. When populated (bed / couch footprint), a **non-rapid** horizontal rest inside that polygon is **suppressed**. A **rapid** transition that ends on the bed footprint can still raise an alarm (impact fall onto bed).

Confidence after a confirmed SZ event: **high** if the last transition was rapid, else **medium**.

**BZ (bathroom) — no furniture zone**

1. **Horizontal + on floor** inside the BZ thermal polygon, motionless for **`bz_still_horizontal_s` (20 s)** → fall **medium** confidence. There is no bed/couch ambiguity: a long, still horizontal floor blob is inherently suspicious.
2. **Water leak** (Aqara Zigbee wet) **true** at overlap with the thermal condition → **high** confidence (“thermal on floor + water on floor” for shower slip).

**Implementation and tuning**

- Python module: **`thermal_fall_detection.PrivacyThermalFallDetector`** at repo root.  
- Thresholds: **`thermal_fall_detection`** block in `cameras_config.json` (kept in sync by `generate_camera_plan.py`).

### Pipeline integration

- Thermal sensors run ESPHome → MQTT → same FastAPI server as the cameras.
- API contract example: `POST /events` with  
  `{ "type": "fall", "room": "SZ" | "BZ", "confidence": "high" | "medium", "ts": <unix> }`  
  and for BZ optionally `"water_leak": true` when the leak sensor is wet.
- Latency budget: on the order of **tens of seconds** gate by design (confirmation windows), not sub-second like camera tracking — there is no rush on the first frame.

---

## 3. Smoke test (before bulk ordering)

Same plan as before, just updated for the actual SKU:

1. Buy **1 OEM PoE camera board** + **1 AMG8833 + ESP32 dev kit**.
2. Camera: connect to the Pi 5 over PoE, confirm:
   - 1080p H.264 sub-stream at 15 fps decodes on the Pi 5 hw decoder
   - IR cut-in / cut-out behaves cleanly under real installation lighting
   - 48 h continuous stream with no drop
   - Stock firmware survives until OpenIPC flash; OpenIPC flash succeeds
3. Thermal: ceiling mount, walk + **rapid** lie-down vs **slow** lie-down; verify the state machine in `thermal_fall_detection.py`: **30 s** stillness before SZ alarm, **20 s** for BZ horizontal floor; optional **water_leak** raises BZ severity.
4. **Only after both smoke tests pass**, bulk-order: 7 more camera boards
   + 1 more thermal sensor + spares + the switch + cabling below.

---

## 4. Supporting hardware to buy alongside

| Item                                              | Qty | Approx EUR / unit | Approx total |
|---------------------------------------------------|----:|------------------:|-------------:|
| OEM PoE camera board (5 MP, 2.8 mm M12, IRCUT)    |   8 |               36  |        ~290  |
| 3D-printed / off-the-shelf hide-frames for cams   |   8 |                5  |         ~40  |
| Panasonic AMG8833 (Grid-EYE) 8×8 thermal          |   2 |               25  |         ~50  |
| ESP32 dev board (for AMG8833 host)                |   2 |                8  |         ~16  |
| Aqara water-leak sensor (BZ)                      |   1 |               15  |         ~15  |
| TP-Link TL-SG108PE (8-port PoE+ managed switch)   |   1 |               60  |         ~60  |
| Cat6 cable, 305 m / 1000 ft box                   |   1 |               70  |         ~70  |
| RJ45 ends + crimping tool                         |   1 |               25  |         ~25  |
| Pi 5 8 GB                                         |   1 |               80  |         ~80  |
| Hailo-8L M.2 HAT                                  |   1 |               70  |         ~70  |
| Active SSD (recordings, optional)                 |   1 |               30  |         ~30  |
|                                                   |     |                   | **~ EUR 750**|

`8 cameras = 7 placements (cams 1–7) + 1 spare`.

---

## 5. What NOT to buy

- **Aqara FP2, Apollo R1, any 24/60 GHz mmWave sensor** — empirically
  fail in this building due to metal-wall multipath. Don't re-test.
- **Tapo / TP-Link cloud cameras** — require a cloud account, RTSP
  support is firmware-version-dependent.
- **Ring / Nest / Arlo** — fully cloud-locked. Out of scope.
- **ESP32-CAM with PoE shield** — same reliability issues as the
  current WiFi prototypes, plus a fragile shield connector.
- **8 MP / 4K cameras** — the Pi 5 / Hailo-8L pipeline is sized for
  1080p decode; 4K doubles decode cost for no tracking benefit.
- **Mixing camera SKUs** — each different model = different firmware
  quirks the tracking engine has to special-case.

---

## 6. Privacy / firewall notes (unchanged from 1.4)

- Cameras + thermal sensors + Pi 5 sit on a **separate VLAN with no
  internet route**. Block outbound at the router as a belt-and-braces
  layer on top of the OpenIPC reflash.
- Pi 5 has internet only when the operator SSHes in and explicitly
  enables it (for `apt update`).
- The dashboard server stays on the regular LAN (it serves the UI to
  the user's phone over local WiFi).
- ESPHome firmware on the thermal sensors is built locally; no cloud
  account, no telemetry.
