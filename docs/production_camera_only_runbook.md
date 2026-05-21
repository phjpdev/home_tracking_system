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

As-built mapping confirmed by the installer (CKiekhoefel) on 2026-05-21:

| IP | Layout name | Room | Mount role |
|----|-------------|------|------------|
| 192.168.178.75 | `cam_kwz_sw` | K/WZ | south-west, looks NE |
| 192.168.178.76 | `cam_kwz_nw` | K/WZ | north-west, looks SE |
| 192.168.178.72 | `cam_kwz_ne` | K/WZ | north-east on K/WZ-BZ frame |
| 192.168.178.73 | `cam_kwz_se` | K/WZ | middle-east on K/WZ-BZ frame |
| 192.168.178.71 | `cam_yoga_ne` | Yoga | north-east, looks SW |
| 192.168.178.70 | `cam_yoga_se` | Yoga | south-west on SZ-Yoga frame |
| 192.168.178.74 | `cam_hallway_n` | Hallway | east end, looks W |

Open `/tmp/stills/*.png` and confirm each image actually shows the room
the layout name claims. If two cameras are swapped (likely on first
install — there is no rule that says installer A's first plug-in goes
into layout slot A), simply edit
`tracking_engine/config.multi_camera.yaml` and swap the two `rtsp_url`
values between the offending `name:` rows. Do **not** rename the
`name:` keys themselves — those are referenced by
`camera_calibrations.json` and the placement JSON.

### 2.2 Fix upside-down or sideways cameras (`rotate:` per stream)

Some PoE mounts force the camera body to hang in an unusual orientation
and the firmware does not auto-rotate. Symptom: the saved still has the
ceiling at the bottom, or the floor on the side. Fix is one line per
camera in `config.multi_camera.yaml`:

```yaml
streams:
  - name: cam_yoga_ne
    rtsp_url: "rtsp://192.168.178.71:554/..."
    enabled: true
    rotate: 180        # 0 (default) | 90 | 180 | 270  — degrees clockwise
```

The engine applies the rotation to every grabbed frame **before**
detection, so detections, ByteTrack, the homography, and Re-ID all see
the corrected image. `tools/probe_rtsp.py` honours the same field, so
your saved stills already show what the engine will see — that means
homography calibration in §3 stays correct.

Confirmed at the 2026-05-21 install:

| Layout name | IP | `rotate:` | Reason |
|-------------|----|-----------|--------|
| `cam_yoga_ne`  | .71 | `180` | mount hangs upside-down |
| `cam_yoga_se`  | .70 | `180` | mount hangs upside-down |
| `cam_hallway_n`| .74 | `180` | mount hangs upside-down |
| (others)       |     | `0`   | normal |

Diagnostic workflow when a new camera arrives or someone re-mounts an
existing one:

```bash
# 1. Capture the raw stream (ignore any rotate: in the YAML).
sudo -u tracking ./.venv/bin/python tools/probe_rtsp.py \
    --no-rotate --frames 30 --timeout 8 --save-stills /tmp/stills_raw

# 2. Eyeball /tmp/stills_raw/*.png. Pick a rotation:
#    - ceiling at bottom of image  ->  rotate: 180
#    - room rotated 90deg right    ->  rotate: 270  (i.e. rotate the image 270 cw to undo)
#    - room rotated 90deg left     ->  rotate: 90
# 3. Edit config.multi_camera.yaml, set rotate: <N> on that stream,
#    then re-run probe with rotation applied to confirm:
sudo -u tracking ./.venv/bin/python tools/probe_rtsp.py \
    --frames 5 --timeout 8 --save-stills /tmp/stills_fixed
```

The startup log line `[multi] rotation overrides: cam_yoga_ne=180deg, ...`
shows which cameras have a rotation applied — useful when triaging from
`journalctl`.

> Always do §2.2 **before** §3. Calibrating a homography against an
> upside-down image and then enabling `rotate:` afterwards will silently
> mirror your floor coordinates.

## 3. Calibrate homographies (per camera)

The shipped `tracking_engine/calibration/camera_calibrations.json` uses
`mode: "dummy"` — every position is just a linear interpolation in the
room polygon. **Real production needs a real homography per camera**, or
the (x_mm, y_mm) you POST is meaningless.

### 3.0 Where to run the tool

Calibration **opens a GUI window**. The Pi is headless and X11
forwarding over Tailscale is too laggy to click pixels accurately —
calibrate on your **laptop** against the saved stills, then push the
JSON back to the Pi.

The stills already have any per-camera `rotate:` applied
(`probe_rtsp.py` honours the field), so a homography computed against
the laptop still matches what the engine sees at runtime.

```powershell
# from your Windows laptop
mkdir stills
scp pi@maro-head.tail79e96b.ts.net:/tmp/stills_fixed/*.png .\stills\
```

### 3.1 As-built dimensions from the site (ground truth)

Tape-measured by the installer on 2026-05-21; this overrides any
discrepancy in the architect-derived polygons. Units below are
centimetres.

| Room | Floor extent (cm) | Note |
|------|-------------------|------|
| K/WZ | L-shape: 781 long × 252 narrow; quadratic part 388 × 288 | "quadratic" = kitchen/dining; **measured to kitchen furniture, not wall** (wall is ~130 cm farther) |
| BZ   | 375 × 173 | no camera (privacy zone) |
| SZ   | 410 × 308 | no camera (privacy zone) |
| Hallway | 309 × 87 | one camera, looks west |
| Yoga | 353 × 363 | two cameras |

| Camera | Mount height (cm) |
|--------|-------------------|
| `cam_kwz_sw`, `cam_kwz_ne`, `cam_kwz_se`, `cam_yoga_ne` | 245 |
| `cam_kwz_nw`, `cam_yoga_se`, `cam_hallway_n` | 246 |

The same data is stored under
`as_built_measurements_2026_05_21` at the top of
`camera_placement_plan/output/cameras_config.json` so it is available
programmatically.

### 3.2 Coordinate frame and per-room anchor table

Keep the **envelope NW corner** as `(0, 0)` (no code change, no
re-rendering of the placement plan). For tape-measure work, anchor on
a room's interior NW corner and add a known offset to get the global
coordinate.

| Room   | NW interior corner in envelope frame (mm) | Room east extent (mm, +x) | Room south extent (mm, +y) |
|--------|------------------------------------------|---------------------------|----------------------------|
| K/WZ   | **(2500, 5000)** | 7810 along long axis (to far end of L) | 2880 in quadratic part / 2520 in narrow part |
| Hallway | **(10100, 8000)** | 3090 | 870 |
| Yoga   | **(14100, 5300)** | 3530 | 3630 |

If the client says *"tape mark in K/WZ is 1.5 m east + 0.8 m south of
the K/WZ NW corner"*, the global mm coords are
`(2500 + 1500, 5000 + 800) = (4000, 5800)`. That is what you type into
the calibration tool.

### 3.3 Per-camera procedure on your laptop

For each camera, from the repo root:

```powershell
python tools\calibrate_homography.py `
    --camera cam_kwz_sw `
    --image stills\cam_kwz_sw.png `
    --out tracking_engine\calibration\camera_calibrations.json
```

Then in the window that pops up:

1. Look at the still and pick **at least 6 floor-plane landmarks**
   (8 is better) spread across the visible floor — not all in a line,
   not all clustered in one corner. Good landmarks:
   - room corners where two walls meet the floor,
   - door thresholds,
   - the base of fixed installations (kitchen counter, oven plinth) —
     these are at the **client's "to-furniture" measurement**, not the
     wall, and that is fine as long as the offset you type matches.
2. Left-click each landmark in the still. After every click, the
   terminal prompts for `x_mm y_mm` — type the global envelope
   coordinate (computed from the anchor table above).
3. Press **`c`** once you have ≥ 6 points. The tool prints the **mean
   reprojection residual in mm**.

   > **The 4-point trap**: a homography has 8 degrees of freedom and
   > 4 points give exactly 8 equations, so the fit is *mathematically
   > exact* — `residual = 0.0 mm` is guaranteed regardless of how
   > wrong the world coords are. With 4 points the residual is
   > **meaningless as a quality check**. Always pick ≥ 6 points so
   > the system is over-determined and the residual is a real RMS
   > error you can trust. The tool now prints a warning in this case.

4. Quality bands (only meaningful at n ≥ 5):

   | Residual (mm) | Verdict |
   |---------------|---------|
   | < 150 | great — save and move on |
   | 150 – 250 | acceptable for v1 |
   | > 250 | re-pick (press `r`) with more spread, or ask the client to add tape markers |

5. Repeat for all 7 cameras: `cam_kwz_sw`, `cam_kwz_nw`, `cam_kwz_ne`,
   `cam_kwz_se`, `cam_yoga_ne`, `cam_yoga_se`, `cam_hallway_n`. The
   tool merges into the same JSON file.

### 3.4 What to look for in each of the 7 stills

These notes pair with the 2026-05-21 stills. You may need to rotate
your screen 90° to read foot-of-wall lines clearly in the wide-angle
images.

| Camera | Where it looks | Easy floor landmarks visible in the still |
|--------|---------------|-------------------------------------------|
| `cam_kwz_sw` | from K/WZ SW corner, NE into kitchen/dining | base of kitchen counter (south wall side), oven plinth corner, the round-table area floor (use the table's footprint to triangulate) |
| `cam_kwz_nw` | from K/WZ NW corner, SE across the living area | corner where curtain wall meets floor, edge of orange sofa base (note: sofa moves, prefer the wall–floor line), threshold at far end (BZ door) |
| `cam_kwz_ne` | from the K/WZ-east frame, SW into K/WZ | wall–floor corner behind the round table, base of the ladder (a temporary fixture — do **not** rely on it once construction is done), inner corner where K/WZ narrows toward BZ |
| `cam_kwz_se` | from middle-east, NW into K/WZ along the BZ frame | the floor strip running west, the visible pipe stubs near BZ (their floor exit points have known coordinates from the BZ leak-mount line), curtain–floor seam |
| `cam_yoga_ne` | from Yoga NE, SW into Yoga | wall–floor corner at far end (Yoga SW), curtain rail's floor projection (only if curtain reaches floor), edge of the yellow sofa base |
| `cam_yoga_se` | from Yoga SW (SZ-Yoga frame), NE into Yoga + threshold | the threshold line into the hallway (this is the **Yoga ↔ Hallway boundary**, useful for cross-camera consistency), inner door frame foot |
| `cam_hallway_n` | hallway east end, looking west | both door thresholds along the corridor, both wall–floor lines (corridor width is 87 cm — use that to anchor scale) |

The two highest-leverage points across the whole site are the
**Yoga–Hallway threshold** and the **Hallway–K/WZ threshold**: those
should land at the same global (x, y) regardless of which of the two
neighbouring cameras you click them in. Use that as a cross-camera
sanity check after step 3.3 finishes.

### 3.5 When the residual is too high or a still has no clear landmarks

Some of the construction-phase stills have plywood walls, no
furniture, and no obvious floor markings. For those cameras, ask the
client to put **four pieces of painter's tape on the floor** in the
camera's footprint, then for each tape send back two numbers: distance
east + distance south from a wall corner you and they have agreed on.
Convert with the anchor table in §3.2 and re-run §3.3 for that one
camera.

A tape marker takes ≤ 5 minutes per camera and reliably gets residuals
under 100 mm.

### 3.6 Push the calibration back to the Pi

```powershell
scp tracking_engine\calibration\camera_calibrations.json `
    pi@maro-head.tail79e96b.ts.net:/tmp/camera_calibrations.json
```

On the Pi:

```bash
sudo install -o tracking -g tracking -m 0644 /tmp/camera_calibrations.json \
    /opt/tracking-system/tracking_engine/calibration/camera_calibrations.json
sudo systemctl restart tracking-engine
journalctl -u tracking-engine -n 50 -f
```

Or, preferred for production: commit the file in git, push, and re-run
`sudo bash deploy/scripts/install.sh` on the Pi (the installer rsyncs
`/opt/tracking-system/`).

### 3.7 End-to-end sanity check

With the mock sink running on the Pi (`mock_maro_server.py`) or
`poster.dry_run: true`, walk through each room and watch the JSON. For
every step:

- printed `(x, y)` should stay inside the room polygon for that
  camera's `cam_id`,
- walking east → `x` increases, walking south → `y` increases,
- consecutive ticks should not jump by more than ~500 mm during a
  steady walk.

Any of those failing → re-run `calibrate_homography.py` for that
camera with more, better-spread points.

> Tip: if a camera's `mount.tilt_deg` or yaw changes (someone bumps
> it), re-run calibration for that camera only — `cv2.findHomography`
> only fits the camera you point it at and the JSON merge keeps the
> other six entries intact.

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
| Image upside-down or sideways in `/tmp/stills` | add `rotate: 180` (or `90`/`270`) to that stream in `config.multi_camera.yaml`; see §2.2 |

For deeper troubleshooting: [`plan/MASTER_PLAN.md`](../plan/MASTER_PLAN.md) §13.3.
