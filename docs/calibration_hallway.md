# Hallway camera calibration (`cam_hallway_n`)

The hallway camera sits at the **east end** of the corridor and looks **west** along the
narrow strip (~3.9 m × 0.7 m). It is mounted **sideways** (`rotate: 90`) and uses a
wide-angle lens, so calibration needs the same LED workflow as K/WZ/Yoga **plus**
lens undistortion.

See also: [`calibration_maro_plan_px.md`](calibration_maro_plan_px.md) (general Maro plan-pixel flow).

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

## Step 2 — Lens undistortion (recommended)

**Does not need Maro** — only the hallway camera RTSP (192.168.178.74) and a **printed chessboard**.

Wide-angle distortion bends floor lines and breaks homography. Calibrate once:

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
   with `K`, `dist`, and `image_size` `[704, 576]`.
5. Restart `tracking-calibrate-web` and `tracking-engine` so undistort maps reload.

**Acceptance:** Side-by-side before/after on a still — tile grout lines near the image edges
should be straight.

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
