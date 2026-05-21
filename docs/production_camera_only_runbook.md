# Production rollout — camera tracking only

Concrete first-deploy checklist for the Raspberry Pi 5 in the home, before
the privacy-zone (thermal/leak) hardware ships and before face enrolment.
The system runs the full 7-camera tracking loop and POSTs anonymised
positions to Maro; thermal fall detection and face recognition stay
disabled.

This document supersedes the camera portion of
[deployment_guide.md](deployment_guide.md) for the first iteration; once
sensors arrive, switch back to the full guide.

## 0. Prerequisites

- Raspberry Pi 5 (8 GB) on the LAN, Tailscale-reachable (`ssh tracking-pi`).
- 7 PoE cameras powered up on the same LAN. Note each one's IP and port.
- A workstation with Python and `torch` to export the OSNet model
  (the Pi exports nothing — only consumes ONNX).
- Repo checked out at `/opt/tracking-system` on the Pi.

## 1. One-shot Pi install

```bash
ssh tracking-pi
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-pip git rsync sqlite3
git clone <repo-url> /opt/tracking-system          # or rsync from workstation
cd /opt/tracking-system
sudo ./deploy/scripts/install.sh
```

The installer (see [`deploy/scripts/install.sh`](../deploy/scripts/install.sh)):

- creates the `tracking` system user,
- installs Mosquitto (you can ignore it for now; thermal will use it later),
- creates a venv at `/opt/tracking-system/.venv` with all Python deps,
- drops three systemd units (`tracking-engine`, `tracking-thermal`,
  `tracking-enroll-web`); only the first one needs to be started today,
- sets up the cron jobs for backup + GDPR retention purge.

## 2. Discover camera IPs and probe RTSP

From the Pi:

```bash
# nmap is a quick way to enumerate webcams; replace the subnet with yours
sudo apt install -y nmap
nmap -p 554 --open 192.168.1.0/24
```

Edit `/opt/tracking-system/tracking_engine/config.multi_camera.yaml` and
replace each `rtsp_url: rtsp://CAMERA_IP_N:554/live/sub` with the real
URL for that physical position. The seven layout names are fixed:

| Name | Room | Mount role |
|------|------|------------|
| `cam_kwz_sw` | K/WZ | living-room SW corner, looks NE |
| `cam_kwz_nw` | K/WZ | living-room NW corner, looks SE |
| `cam_kwz_ne` | K/WZ | living-room NE corner |
| `cam_kwz_se` | K/WZ | living-room SE corner |
| `cam_yoga_ne` | Yoga | yoga-room NE corner |
| `cam_yoga_se` | Yoga | yoga-room SE corner |
| `cam_hallway_n` | Hallway | hallway east end |

Then verify each stream decodes from the Pi:

```bash
cd /opt/tracking-system
sudo -u tracking ./.venv/bin/python tools/probe_rtsp.py --frames 30 --timeout 8
```

You should see seven `OK` lines with frame size and FPS. If a camera
fails: re-check IP, RTSP path (try `/live`, `/stream0`, `/h264Preview_01_sub`),
PoE port LED, and credentials in the URL (`rtsp://user:pass@ip:554/...`).
Save one still per camera while you are at it; you will need them for
calibration:

```bash
sudo -u tracking ./.venv/bin/python tools/probe_rtsp.py --save-stills /tmp/stills
ls /tmp/stills
```

## 3. Calibrate homographies (per camera)

The shipped `tracking_engine/calibration/camera_calibrations.json` uses
`mode: "dummy"` — every position is just a linear interpolation in the
room polygon. **Real production needs a real homography per camera**, or
the (x_mm, y_mm) you POST is meaningless.

Open `camera_placement_plan/floor_plan.png` next to the calibration
window so you can read off floor coordinates.

For each camera:

```bash
cd /opt/tracking-system
sudo -u tracking ./.venv/bin/python tools/calibrate_homography.py \
    --camera cam_kwz_sw \
    --image /tmp/stills/cam_kwz_sw.png
```

Click **at least 4** floor-plane points spread across the visible floor:
door sills, parquet seams, table-leg footprints, room corners — anything
you can locate on both the image and the floor plan in millimetres.
After each click, type the corresponding `x_mm y_mm` (from the floor
plan, origin = NW corner). When you have ≥ 4 points, press `c` to
compute the homography. Aim for a mean reprojection residual under
~150 mm; if it is larger, press `r` and re-pick points with better
spread.

Repeat for all 7 cameras. The script merges into
`tracking_engine/calibration/camera_calibrations.json` per camera; other
entries are preserved.

> Tip: if a camera's `mount.tilt_deg` or yaw changes (someone bumps it),
> re-run calibration for that camera only.

## 4. Body Re-ID model (OSNet ONNX)

Run **on a workstation** (the Pi has no `torch`):

```bash
python -m venv /tmp/onnx-env
source /tmp/onnx-env/bin/activate
pip install torch torchreid onnx onnxsim onnxruntime
python tools/export_osnet_onnx.py \
    --out tracking_engine/models/osnet_x025.onnx \
    --quantize tracking_engine/models/osnet_x025_int8.onnx
```

Then copy the INT8 file to the Pi:

```bash
rsync tracking_engine/models/osnet_x025_int8.onnx \
    tracking-pi:/opt/tracking-system/tracking_engine/models/
sudo chown tracking:tracking /opt/tracking-system/tracking_engine/models/*.onnx
```

In `config.multi_camera.yaml`:

```yaml
reid:
  enabled: true
  body:
    onnx_model_path: models/osnet_x025_int8.onnx
  face:
    enabled: false       # not yet — leave off until Phase D
```

If you are in a hurry and want to start the engine without OSNet, leave
`reid.enabled: false`. The system will track within a single camera but
will not assign cross-camera global IDs.

## 5. First boot — dry run against the mock sink

Until the Maro service is reachable from the Pi, keep `poster.dry_run: true`
in the config. To watch the payloads, run the mock sink in one shell and
the engine in another (both on the Pi):

```bash
# Terminal A — sink that prints every POST body
sudo -u tracking /opt/tracking-system/.venv/bin/python \
    /opt/tracking-system/tracking_engine/mock_maro_server.py 8765

# Terminal B — switch dry_run -> false, point at the local sink, then run
sudo -u tracking /opt/tracking-system/.venv/bin/python \
    -m tracking_engine.multi_camera \
    --config /opt/tracking-system/tracking_engine/config.multi_camera.yaml
```

You should see `tick=...  grab=... detect=... track=... post=...` lines
and one JSON line per camera per tick in terminal A:

```json
{"endpoint":"positions","payload":{"cam_id":"cam_kwz_sw","ts":1763155923.412,
  "persons":[{"id":"t1","x":4500,"y":7100,"zone":"K/WZ","privacy":false,
              "global_id":"...","track_state":"confirmed","reid_score":0.18}]}}
```

Stop with `Ctrl-C` once the loop is stable.

## 6. Point at Maro and start the systemd service

Edit `config.multi_camera.yaml`:

```yaml
poster:
  url: http://maro.lan:8000/tracking/positions
  events_url: http://maro.lan:8000/tracking/events
  dry_run: false

observability:
  metrics_enabled: true
  log_dir: /var/log/tracking-engine
```

Start the service:

```bash
sudo systemctl enable --now tracking-engine
sudo systemctl status tracking-engine
journalctl -u tracking-engine -f
```

Leave `tracking-thermal.service` and `tracking-enroll-web.service`
**disabled** for now:

```bash
sudo systemctl disable --now tracking-thermal tracking-enroll-web
```

## 7. Inspect the database

The gallery is a single SQLite file at
`/opt/tracking-system/reid_gallery.db` with a FAISS sidecar at
`reid_gallery.faiss`. To answer "what is in there right now":

```bash
cd /opt/tracking-system
sudo -u tracking ./.venv/bin/python -m tracking_engine.tools.inspect_gallery
```

Sample output:

```text
config:        /opt/.../config.multi_camera.yaml
sqlite:        /opt/.../reid_gallery.db  (320.4 KiB)
faiss vectors: body=42  face=0
table row counts:
  appearance_embedding             42
  consent_record                    0
  fusion_event                      3
  global_track                     11
  identity                          0
  ...
recent global tracks (top 10):
  4f37e9d2…  status=confirmed       protos=  6  last=2026-05-21 14:08:11  identity=-
  ...
```

Other ways:

```bash
# raw SQL prompt
sudo -u tracking sqlite3 /opt/tracking-system/reid_gallery.db
sqlite> .tables
sqlite> SELECT global_track_id, status, last_seen FROM global_track ORDER BY last_seen DESC LIMIT 10;

# JSON for ingesting elsewhere
sudo -u tracking ./.venv/bin/python -m tracking_engine.tools.inspect_gallery --json --since 1h
sudo -u tracking ./.venv/bin/python -m tracking_engine.tools.list_identities
```

The schema is documented in
[`tracking_engine/reid/gallery_sqlite.py`](../tracking_engine/reid/gallery_sqlite.py)
under `_ensure_schema`.

## 8. Day-to-day operations (camera-only mode)

| Task | Command |
|------|---------|
| Tail logs | `journalctl -u tracking-engine -f` |
| Probe all cameras | `python tools/probe_rtsp.py` |
| Re-calibrate one camera | `python tools/calibrate_homography.py --camera <name> --rtsp ...` |
| Inspect gallery | `python -m tracking_engine.tools.inspect_gallery` |
| Manual restart | `sudo systemctl restart tracking-engine` |
| Live metrics | `curl http://<pi>:9100/metrics` |
| Backups | `/var/backups/tracking-engine/` (cron 03:30) |
| Wipe gallery (factory reset) | `sudo -u tracking rm /opt/tracking-system/reid_gallery.* && sudo systemctl restart tracking-engine` |

## 9. What's intentionally not enabled yet

| Feature | Status | Re-enable when |
|---------|--------|----------------|
| Thermal MLX90640 + ESP32 nodes | hardware not yet shipped | sensors arrive — see [`plan/MASTER_PLAN.md`](../plan/MASTER_PLAN.md) §6 |
| Fall-event POST | depends on thermal | same |
| Face recognition (`reid.face.enabled`) | needs SCRFD + ArcFace ONNX + per-person consent | enrolment day; see [`enrollment_user_guide.md`](enrollment_user_guide.md) |
| Whole-disk encryption (LUKS) | optional | before storing face embeddings; see [`gdpr/luks_setup.md`](gdpr/luks_setup.md) |
| Face-embedding key | only matters when face is on | before flipping face on |

## 10. When tracking misbehaves

| Symptom | First check |
|---------|-------------|
| Engine restarts every few seconds | `journalctl -u tracking-engine -n 200` — usually a bad RTSP URL |
| `(x_mm, y_mm)` is obviously wrong | re-run `tools/calibrate_homography.py` for that camera |
| `[multi] WARNING: re-id is using the grayscale fallback embedder` | OSNet ONNX file missing or wrong path; verify §4 |
| `POST failed: ... Connection refused` | start mock sink (§5) or set `poster.dry_run: true` |
| Two people share one `global_id` | lower `reid.body.threshold_match` by 0.05; tune in `phase_a5_tuning.md` |
| One person gets two `global_id` | raise `reid.body.threshold_match` by 0.05 |
| 100% CPU on one core | sub-stream is missing; switch all RTSP URLs to the 640×480/15 fps sub-stream |

For deeper troubleshooting: [`plan/MASTER_PLAN.md`](../plan/MASTER_PLAN.md) §13.3.
