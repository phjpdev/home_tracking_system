# Deployment Guide

End-to-end install on the Raspberry Pi 5 in the house. Companion to
[../plan/MASTER_PLAN.md](../plan/MASTER_PLAN.md) section 13.

## 1. Physical install

Per [../camera_placement_plan/README.md](../camera_placement_plan/README.md).

1. Cat6 runs from each camera and each privacy-zone ESP32 node back to
   the patch panel near the Pi.
2. All cabling on PoE — the switch (TP-Link TL-SG1008P or Ubiquiti
   USW-Lite-8-PoE) powers cameras and ESP32 nodes.
3. Cameras: 7 OEM PoE boards behind 3D-printed corner enclosures.
   Bracket coordinates: [../camera_placement_plan/README.md](../camera_placement_plan/README.md) Section 1.
4. Privacy nodes: 2 Olimex ESP32-POE-ISO with MLX90640 thermal grids,
   ceiling mounts per [../esphome/README.md](../esphome/README.md). BZ
   additionally hosts a wired conductive leak probe at the shower egress.

## 2. Pi 5 base install

```bash
# Flash Raspberry Pi OS 64-bit Lite Bookworm, log in.
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-pip git rsync sqlite3 cron
```

## 3. Pull the repo and run the installer

```bash
git clone <repo-url> /opt/tracking-system   # or rsync from the workstation
cd /opt/tracking-system
sudo ./deploy/scripts/install.sh
```

The installer:

- Installs OS packages (mosquitto, mosquitto-clients).
- Creates the `tracking` system user and `/opt/tracking-system` layout.
- Creates a venv and installs Python deps from
  [../tracking_engine/requirements.txt](../tracking_engine/requirements.txt).
- Installs the three systemd units.
- Installs `/etc/cron.d/tracking-system` (backup + retention purge).
- Sets up Mosquitto (interactive password prompt for the `tracking`
  user; reuse this same password in `esphome/secrets.yaml`).

## 4. Generate the face-embedding encryption key

```bash
sudo -u tracking /opt/tracking-system/.venv/bin/python \\
    -m tracking_engine.reid.crypto \\
    --generate /etc/tracking-engine/secret.key
sudo chmod 0640 /etc/tracking-engine/secret.key
sudo chown root:tracking /etc/tracking-engine/secret.key
```

Without this key, face embeddings are stored unencrypted; you will see
a loud warning in the logs. **Do not** deploy with face enabled and the
warning present.

## 5. Export the ONNX models

On a workstation with `torch` available:

```bash
python tools/export_osnet_onnx.py \\
    --out tracking_engine/models/osnet_x025.onnx \\
    --quantize tracking_engine/models/osnet_x025_int8.onnx
```

Download SCRFD-500MF + ArcFace MFN from the `insightface` model zoo
(see [../tracking_engine/models/README.md](../tracking_engine/models/README.md)).
Copy all three ONNX files to `/opt/tracking-system/tracking_engine/models/` on the Pi.

## 6. Calibrate cameras

Per the homography tool in [../tracking_engine/pipeline/homography.py](../tracking_engine/pipeline/homography.py).
Results live in [../tracking_engine/calibration/camera_calibrations.json](../tracking_engine/calibration/camera_calibrations.json).

Re-run after any furniture or camera move that changes ground points.

## 7. Configure

Edit `/opt/tracking-system/tracking_engine/config.multi_camera.yaml`:

```yaml
multi_camera:
  streams:
    - { name: cam_kwz_sw, rtsp_url: rtsp://camera-ip-1:554/live, enabled: true }
    - { name: cam_kwz_nw, rtsp_url: rtsp://camera-ip-2:554/live, enabled: true }
    # ...

poster:
  url: http://maro.lan:8000/tracking/positions
  events_url: http://maro.lan:8000/tracking/events
  dry_run: false

reid:
  enabled: true
  face:
    enabled: true              # only after enrolling at least one person

thermal:
  enabled: true
  mqtt:
    user: tracking
    password: <pi password chosen during install>

observability:
  metrics_enabled: true
  log_dir: /var/log/tracking-engine
```

## 8. Flash the privacy nodes

Per [../esphome/README.md](../esphome/README.md). USB cable for first
flash, OTA after that.

## 9. Start services

```bash
sudo systemctl enable --now tracking-engine tracking-thermal tracking-enroll-web
sudo systemctl status tracking-engine tracking-thermal tracking-enroll-web
```

Sanity checks:

```bash
mosquitto_sub -u tracking -P <pw> -t 'home/+/node/heartbeat'
curl http://127.0.0.1:9100/metrics | head
journalctl -u tracking-engine -f
```

## 10. Enroll family members

Browser flow (recommended): open `http://<pi-ip>:8088` and follow
[enrollment_user_guide.md](enrollment_user_guide.md).

CLI flow:

```bash
sudo -u tracking /opt/tracking-system/.venv/bin/python -m tracking_engine.enroll \\
    --camera cam_kwz_sw \\
    --name "Jean Patrick" \\
    --frames 5 \\
    --consent-basis consent
```

## 11. Verify a POST round-trip

```bash
journalctl -u tracking-engine -n 50 -f | grep '"persons"'
```

You should see one POST per camera per tick when people are visible.
With face enrolled, `identity_name` appears next to confirmed tracks.

## 12. Daily operations

| Task | Tool |
|------|------|
| Tail logs | `journalctl -u tracking-engine -f` |
| Live metrics | `http://<pi-ip>:9100/metrics`, plug into Grafana |
| Backups | `/var/backups/tracking-engine/YYYY-MM-DD/` (cron at 03:30) |
| Retention purge | `/var/log/syslog` for `[purge]` lines (cron at 03:45) |
| List identities | `python -m tracking_engine.tools.list_identities` |
| Delete identity (GDPR erasure) | `python -m tracking_engine.tools.delete_identity --id <uuid>` |
| Revoke consent | `python -m tracking_engine.tools.revoke_consent --id <uuid>` |
| Merge two identities | `python -m tracking_engine.tools.merge_identities --from <uuid> --into <uuid>` |
| Split a global track | `python -m tracking_engine.tools.split_identity --gid <uuid> --since <epoch>` |

## 13. Troubleshooting

See [../plan/MASTER_PLAN.md](../plan/MASTER_PLAN.md) section 13.3 for the
symptom-to-fix table.
