# Privacy zone (SZ/BZ) — production runbook

End-to-end guide for MLX90640 thermal nodes in the bedroom (SZ) and bathroom (BZ):
flash, mount, soak test, enable live dots on Maro, fall detection, and on-site tuning.

**Related:** [`esphome/README.md`](../esphome/README.md), [`phase_c_tuning.md`](phase_c_tuning.md),
[`soak_test_phase_b.txt`](soak_test_phase_b.txt), [`production_camera_only_runbook.md`](production_camera_only_runbook.md).

---

## What the privacy pipeline does

| Output | Endpoint | When |
|--------|----------|------|
| **Live dot** on Maro floor plan | `POST /tracking/positions` | Person in SZ/BZ (~2 Hz, coarse ~15–25 cm) |
| **Fall alarm** | `POST /tracking/events` | Horizontal still + timing rules |
| **Presence MQTT** | `home/<room>/thermal/presence` | For optical boundary suppress (`thermal_gate`) |

No optical cameras in SZ/BZ. No homography calibration — geometry comes from ceiling mount + FOV in
[`camera_placement_plan/output/cameras_config.json`](../camera_placement_plan/output/cameras_config.json).

---

## Prerequisites

- Raspberry Pi 5 with Mosquitto (`deploy/scripts/install.sh`)
- Maro running on `:8420` (same Pi)
- PoE switch; ESP32-POE-ISO boards flashed
- Optical cameras calibrated (K/WZ, Yoga, Hallway) if using `thermal_gate`
- Repo deployed at `/opt/tracking-system`

---

## 1. Flash ESP32 nodes

On a machine with USB (first time only):

```bash
pip install esphome
cp esphome/secrets.example.yaml esphome/secrets.yaml   # edit Wi-Fi / MQTT / OTA
esphome run esphome/sz_node.yaml
esphome run esphome/bz_node.yaml
```

Subsequent updates: OTA over LAN.

---

## 2. Mount hardware

| Room | Thermal mount (plan mm) | Also |
|------|-------------------------|------|
| SZ | (12150, 6500, 3000) | Door reed on SZ door |
| BZ | centroid of BZ polygon, z=3000 | Door reed + leak probe at shower egress |

After install, if the dot is systematically shifted, edit `thermal.mount_offset_mm` in
[`config.multi_camera.yaml`](../tracking_engine/config.multi_camera.yaml) or update mounts in
`generate_camera_plan.py` and regenerate `cameras_config.json`.

---

## 3. Phase B soak test

Fill [`soak_test_phase_b.txt`](soak_test_phase_b.txt).

```bash
mosquitto_sub -h 127.0.0.1 -u tracking -P '<pw>' -t 'home/+/thermal/frame' | head
mosquitto_sub -h 127.0.0.1 -u tracking -P '<pw>' -t 'home/+/node/heartbeat'
mosquitto_sub -h 127.0.0.1 -u tracking -P '<pw>' -t 'home/+/door/state'
mosquitto_sub -h 127.0.0.1 -u tracking -P '<pw>' -t 'home/bz/leak/state'
```

Targets: ≥ 4 Hz frames for 30 min both rooms; door < 250 ms; leak < 500 ms; no offline heartbeat in 6 h.

---

## 4. Enable thermal services

Edit `/opt/tracking-system/tracking_engine/config.multi_camera.yaml`:

```yaml
thermal:
  enabled: true
  dry_run: false          # start with true to verify logs only
  positions:
    enabled: true
  mqtt:
    publish_presence: true

tracking:
  thermal_gate:
    enabled: true         # after optical cams calibrated
    boundary_buffer_px: 80.0
```

```bash
sudo systemctl enable --now tracking-thermal
sudo systemctl restart tracking-engine    # picks up thermal_gate
sudo journalctl -u tracking-thermal -f
```

Metrics (optional): `observability.metrics_enabled: true` → `curl http://127.0.0.1:9101/metrics`

---

## 5. Verify live dots

### Option A — Maro UI

Walk SZ and BZ. Coarse dot should appear in the room zone (`privacy: true`, `position_source: thermal`).

### Option B — mock sink (dev)

```bash
python tracking_engine/mock_maro_server.py
# point poster.url and thermal dry_run false to :8765
```

### Option C — live tuning CLI

```bash
python tools/thermal_live_view.py --host 127.0.0.1 --room sz
python tools/thermal_live_view.py --host 127.0.0.1 --room bz --body-min 29 --show
```

---

## 6. Fall detection tuning

Run scenarios in [`phase_c_tuning.md`](phase_c_tuning.md). Adjust:

- `thermal.detection.*` in `config.multi_camera.yaml`
- `thermal_fall_detection` in `cameras_config.json` (regenerate from `generate_camera_plan.py`)

Restart after changes:

```bash
sudo systemctl restart tracking-thermal
```

---

## 7. Deploy calibration / config to git

Thermal geometry lives in `cameras_config.json`, not `camera_calibrations.json`.

```bash
sudo cp /opt/tracking-system/tracking_engine/config.multi_camera.yaml \
  ~/home_tracking_system/tracking_engine/config.multi_camera.yaml
# commit + push from ~/home_tracking_system
```

After `git pull` on Pi, run `install.sh` only **after** copying any local cal changes into the repo.

---

## 8. Rollback

```bash
sudo systemctl stop tracking-thermal
# set thermal.enabled: false in config
sudo systemctl restart tracking-engine
```

Optical-only mode continues on PoE cameras.

---

## Appendix A — Maro integration checklist

Maro (`maro-clean`, separate repo) must accept:

| Check | Requirement |
|-------|-------------|
| `POST /tracking/positions` | Accept `cam_id: thermal_sz` / `thermal_bz` (no whitelist blocking unknown cams) |
| Person fields | `zone: SZ|BZ`, `privacy: true`, `position_source: thermal` |
| Coordinates | Legacy mm on wire (tracking engine rescales from plan px automatically) |
| `POST /tracking/events` | Accept `event_type: fall`, `room`, `confidence`, optional `water_leak` |
| Zone polygons | Maro `/api/zones` includes **SZ** and **BZ** matching plan layout |
| UI | Render privacy-room dots (optional distinct style for `privacy: true`) |

Verify with:

```bash
curl -s http://127.0.0.1:8420/api/zones | head
journalctl -u tracking-thermal -f   # look for "position POST" / "fall event posted"
```

If Maro does not implement `/tracking/events` yet, falls log to stderr but positions still work.

---

## Appendix B — systemd units

| Service | Port | Role |
|---------|------|------|
| `tracking-thermal` | MQTT in, HTTP out | SZ/BZ blobs, positions, falls |
| `tracking-engine` | RTSP in | PoE cameras + optional `thermal_gate` |
| Mosquitto | 1883 | Broker |
| Maro | 8420 | Floor plan UI + position sink |

---

## Appendix C — accuracy expectations

MLX90640 is **32×24** pixels at ~3 m ceiling. Expect **room-scale** dots (~15–25 cm), not camera-grade paths.
Use for zone presence, coarse position, and falls — not mm-precise furniture tracking.
