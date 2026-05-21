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

- Raspberry Pi 5 (8 GB) on the home LAN `192.168.178.0/24`, reachable
  via Tailscale (the Pi is `maro-head`, Tailscale IP
  `100.95.218.102`, shared in by `CKiekhoefel@github`).
- 7 PoE cameras powered up at `192.168.178.70` … `192.168.178.76`.
- A workstation with Python and `torch` to export the OSNet model
  (the Pi exports nothing — only consumes ONNX).
- Repo checked out at `/opt/tracking-system` on the Pi.

## 1. One-shot Pi install

```bash
ssh maro@maro-head.tail79e96b.ts.net   # use the Linux user the owner created
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-pip git rsync sqlite3

# Clone anywhere — the installer self-locates the repo root and copies into /opt.
mkdir -p ~/src && cd ~/src
git clone <repo-url> tracking
cd tracking

# Always invoke with ``bash`` so the missing execute-bit on git-from-Windows
# checkouts does not bite. The script is idempotent — safe to re-run.
sudo bash deploy/scripts/install.sh
```

The installer (see [`deploy/scripts/install.sh`](../deploy/scripts/install.sh)):

- self-detects the repo root from its own path,
- creates the `tracking` system user,
- rsyncs the repo into `/opt/tracking-system` and restores `+x` on
  every `deploy/scripts/*.sh` (Windows git checkouts often drop it),
- installs Mosquitto (you can ignore it for now; thermal will use it later),
- creates a venv at `/opt/tracking-system/.venv` with all Python deps,
- drops three systemd units (`tracking-engine`, `tracking-thermal`,
  `tracking-enroll-web`); only the first one needs to be started today,
- sets up the cron jobs for backup + GDPR retention purge.

Common first-time failure modes:

| Error | Fix |
|-------|-----|
| `sudo: ./install.sh: command not found` | The script lost its execute bit during `git clone` (Windows checkout). Use `sudo bash deploy/scripts/install.sh` instead of `./install.sh`. |
| `sanity check failed: tracking_engine/requirements.txt missing` | You ran the script from the wrong directory and `SRC_DIR` defaulted incorrectly. Either `cd` to the repo root or run with `SRC_DIR=/path/to/repo sudo -E bash deploy/scripts/install.sh`. |
| `bad interpreter: /usr/bin/env bash^M` | CRLF line endings in the script. Run `sudo apt install -y dos2unix && find deploy/scripts -name '*.sh' -exec dos2unix {} +` and try again. |
| `Job for mosquitto.service failed because the control process exited with error code` | Mosquitto is only needed for Phase B/C. Re-run with `SKIP_MQTT=1 sudo -E bash deploy/scripts/install.sh` or just continue — camera tracking is unaffected. Fix later with `sudo systemctl status mosquitto` + `sudo journalctl -xeu mosquitto.service` (typical cause: a stale `listener 1883` already bound from a previous install). |
| `apt-get: command not found` mid-run | You're not on Debian/Raspberry Pi OS. The script is Pi-OS specific. |

### 1.1 Edit-and-redeploy loop with git

The installer **rsyncs** the repo into `/opt/tracking-system`, which is
where the systemd service runs from. Your git clone at
`/home/pi/home_tracking_system` is the source of truth; `/opt/tracking-system`
is the live copy. Two ways to iterate:

```bash
# Option A — pull a change you committed on your workstation:
cd ~/home_tracking_system && git pull
sudo bash deploy/scripts/install.sh    # rsyncs into /opt/tracking-system

# Option B — quick local edit on the Pi for triage (overwritten on next pull/install):
sudo -u tracking nano /opt/tracking-system/tracking_engine/config.multi_camera.yaml
sudo systemctl restart tracking-engine
```

Anything stable you change on the Pi: copy back into `~/home_tracking_system`,
commit, and push, otherwise the next install run will silently overwrite it.

## 2. Probe the seven RTSP streams

The home runs on `192.168.178.0/24` (FRITZ!Box subnet). Cameras live at
`192.168.178.70 … .76`. Each board exposes two RTSP streams using
inline credentials in the query string:

```text
rtsp://192.168.178.{70..76}:554/user=admin&password=admin123&channel=1&stream={0|1}.sdp
```

| `stream=` | Resolution | Codec | Use |
|-----------|-----------|-------|-----|
| `0` | 2880×1620 | H.265 | high-quality archive (we don't run YOLO on this) |
| `1` | 640×480 | H.264 | tracking — Pi 5 CPU is fine here |

`config.multi_camera.yaml` already contains all 7 URLs with `stream=1`.
Rotate `admin123` to a stronger password as soon as the system is
stable; cameras are LAN-only but the FRITZ!Box admin account and the
camera admin account share the password until you change it.

Verify all seven streams decode from the Pi:

```bash
cd /opt/tracking-system
sudo -u tracking ./.venv/bin/python tools/probe_rtsp.py --frames 30 --timeout 8 \
    --save-stills /tmp/stills
ls /tmp/stills
```

Expected output (approximate):

```text
[probe] -> cam_kwz_sw  rtsp://192.168.178.70:554/...
   OK  frames= 30  size=640x480  fps~14.6  in 2.1s  still=/tmp/stills/cam_kwz_sw.png
[probe] -> cam_kwz_nw  rtsp://192.168.178.71:554/...
   OK  ...
...
[probe] summary: 7/7 OK
```

If a camera fails: check the PoE port LED, ping its IP, confirm the
URL path (some firmwares use `/cam/realmonitor?channel=1&subtype=1`
or `/Streaming/Channels/102` instead — try a couple of variants in a
browser-side `vlc rtsp://...` test).

### 2.1 Confirm the IP → layout-name mapping

The seven layout names in `cameras_config.json` are physical-position
keyed (e.g. `cam_kwz_sw` is mounted at the south-west corner of the
living room). The IP order in `config.multi_camera.yaml` is the
**working assumption** — sorted by IP, room-major:

| IP | Layout name | Room | Mount role |
|----|-------------|------|------------|
| 192.168.178.70 | `cam_kwz_sw` | K/WZ | south-west, looks NE |
| 192.168.178.71 | `cam_kwz_nw` | K/WZ | north-west, looks SE |
| 192.168.178.72 | `cam_kwz_ne` | K/WZ | north-east on K/WZ-BZ frame |
| 192.168.178.73 | `cam_kwz_se` | K/WZ | middle-east on K/WZ-BZ frame |
| 192.168.178.74 | `cam_yoga_ne` | Yoga | north-east, looks SW |
| 192.168.178.75 | `cam_yoga_se` | Yoga | south-west on SZ-Yoga frame |
| 192.168.178.76 | `cam_hallway_n` | Hallway | east end, looks W |

Open `/tmp/stills/*.png` and confirm each image actually shows the room
the layout name claims. If two cameras are swapped (likely on first
install — there is no rule that says installer A's first plug-in goes
into layout slot A), simply edit
`tracking_engine/config.multi_camera.yaml` and swap the two `rtsp_url`
values between the offending `name:` rows. Do **not** rename the
`name:` keys themselves — those are referenced by
`camera_calibrations.json` and the placement JSON.

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
