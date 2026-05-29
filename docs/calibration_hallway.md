# Hallway camera calibration (`cam_hallway_n`)

The hallway camera sits at the **east end** of the corridor and looks **west** along the
narrow strip (~3.9 m × 0.7 m). It is mounted **sideways** (`rotate: 90`) and uses a
wide-angle lens, so calibration needs the same LED workflow as K/WZ/Yoga **plus**
lens undistortion.

See also: [`calibration_maro_plan_px.md`](calibration_maro_plan_px.md) (general Maro plan-pixel flow).

**Client-facing step-by-step:** [`hallway_calibration_client_guide.md`](hallway_calibration_client_guide.md)
— a self-contained walkthrough (lights on, capture still, fix distortion on a Mac, deploy, lights off).

## Prerequisites

- Maro API up and `maro_cache` populated (real `floorplan_bg.png`, `strips.json`).
- `calibrate_web` on the Pi (`/opt/tracking-system/.venv`).
- Art-Net reachable at `192.168.178.10:6454` for LED markers (optional but recommended).
- Config: [`tracking_engine/config.multi_camera.yaml`](../tracking_engine/config.multi_camera.yaml)
  — `cam_hallway_n.rotate: 90`.

## Step 1 — Capture a rotated still

After any mount change, refresh the still **with rotation applied**:

```bash
cd /opt/tracking-system
python tools/probe_rtsp.py --camera cam_hallway_n --save-stills /tmp/stills_hallway
```

Copy `cam_hallway_n.png` into `stills/` if calibrating from a laptop.

## Step 2 — Lens undistortion (recommended, optional)

This step is a **quality upgrade, not a requirement**. The hallway is calibrated
with the **same LED + Line homography** as K/WZ and Yoga (Step 3). Because the
hallway lens is heavily wide-angle and the camera is mounted sideways, a single
homography fits poorly across the long corridor — a person walking "sweeps"
in/out along the axis. Removing lens distortion first straightens the floor so
the homography lands accurately end-to-end. **Does not need Maro.**

You have two ways to get the distortion coefficients; both write the same
`cam_hallway_n` entry into `camera_intrinsics.json`.

### Option A — Chessboard (classic)

Needs a **printed chessboard** and the live RTSP stream.

1. Print a chessboard (default inner corners **9×6**, 25 mm squares — count **inner** corners, not squares).
2. Verify the camera first:
   ```bash
   /opt/tracking-system/.venv/bin/python tools/probe_rtsp.py \
     --config tracking_engine/config.multi_camera.yaml \
     --camera cam_hallway_n --save-stills /tmp/hallway_test
   ```
3. Wave the board through the hallway camera FOV while capturing frames (use venv):

```bash
cd /opt/tracking-system
/opt/tracking-system/.venv/bin/python tools/calibrate_camera_intrinsics.py \
  --camera cam_hallway_n \
  --config tracking_engine/config.multi_camera.yaml \
  --frames 40 --settle-sec 5 \
  --save-debug /tmp/hallway_debug
```

If you get `got 0 chessboard detections`: no checkerboard was seen (wrong pattern size, board too small/far, or RTSP not delivering frames). Check `/tmp/hallway_debug/last_frame_no_chessboard.jpg`.

4. Confirm `tracking_engine/calibration/camera_intrinsics.json` contains `cam_hallway_n`
   with `K`, `dist`, and `image_size` matching the rotated frame.
5. Restart `tracking-calibrate-web` and `tracking-engine` so undistort maps reload.

### Option B — Line-based (no chessboard, uses the tiles)

When a chessboard is impractical, use features that are physically straight —
floor **tile grout lines** (a 2D grid is ideal), the **LED strip**, and a
**wall base**. A small GUI lets you click points along each line on a lit still
and solves for the distortion that makes them straight again (plumb-line
method). Run on a machine with a display (e.g. a Mac) using the rotated still
from `probe_rtsp.py`:

```bash
python tools/calibrate_distortion_lines.py \
  --image stills/cam_hallway_n.png --camera cam_hallway_n
```

Click >=3 points per line, `n` for next line, add several lines in both
directions, then `c` to compute and `s` in the preview to save. Aim for the
"line-straightness RMS" to drop to ~1px or less.

**Acceptance (either option):** Side-by-side before/after on a still — tile grout
lines near the image edges should be straight.

## Step 3 — Calibrate homography (LED + Line mode)

1. Start UI: `sudo systemctl start tracking-calibrate-web` (or foreground venv).
2. Open `http://<pi>:8090` (SSH `-L 8090:127.0.0.1:8090` from laptop).
3. Enable **LED markers** (50 cm ON / 50 cm OFF on hallway strip).
4. Select **Line** mode:
   - On the Maro plan: click **start** and **end** of the hallway along the corridor
     (east → west, matching the LED strip).
   - Use **N = 6–8** interpolated landmarks.
5. On **`cam_hallway_n` tile**: click **two endpoints** of the visible lit LED run in the
   image (tool fills intermediate points).
6. Switch to **Point** mode: add **one** landmark off the line (e.g. Yoga–hallway threshold)
   to avoid a degenerate homography.
7. Target **green ✓** (mean residual ≤ 5 px, ≥ 6 points).
8. **Download session** → **Save**.

Verify saved JSON:

```bash
grep -E '_coordinate_space|cam_hallway_n|world_points_plan_px' \
  tracking_engine/calibration/camera_calibrations.json | head -20
```

## Step 4 — Runtime check

```bash
sudo systemctl restart tracking-engine
journalctl -u tracking-engine -n 30
```

Expect `coords=maro_plan_px fused_post`.

**Walk test:** one person walks the hallway; **one** dot on Maro within ~10 px, minimal spikes.

Turn **LED markers OFF** when finished.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| High residual only on hallway | Re-run intrinsics; use Line mode; spread landmarks along full FOV |
| Dot jumps / spikes | Undistort first; tune `max_speed_pxps` / `ema_alpha` in config |
| Plan overlay misaligned | Re-fetch `maro_cache`; confirm Y-axis matches Maro UI |
| LED stay on | Toggle off in UI or `POST /api/led/off` |

## Client action (Maro)

Add a **Hallway / Flur** zone polygon in Maro `zones.json` so `drop_outside_zones: true`
can be re-enabled in config.
