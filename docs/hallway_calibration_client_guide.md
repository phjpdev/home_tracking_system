# Hallway calibration — step-by-step guide

This guide lets you calibrate the hallway camera (`cam_hallway_n`) yourself. The
hallway is harder than K/WZ and Yoga because the camera is mounted **sideways**
and uses a **wide-angle lens** that bows straight lines — that bowing is what
makes a walking person's dot "sweep" up/down the corridor. We fix it in two
parts:

1. **Light the corridor** so the camera can see (it is very dark otherwise).
2. **Remove the lens distortion** using the straight lines already in the room
   (floor tiles, LED strip, wall) — no chessboard needed.
3. Later, when Maro is running, do the normal **LED + Line** homography like the
   other cameras.

You need:

- SSH access to the Pi (`maro-head`) — directly or over Tailscale.
- A **Mac/PC with a screen** and Python 3 (for the click tool in Part C).
- The repo checked out on that Mac/PC.

---

## Part A — Turn the hallway spotlights ON

The spotlights are on the DMX controller (ODE MK3, `192.168.178.11`). A helper
script turns all of them on and **guarantees** they turn off again.

On the **Pi**:

```bash
cd /opt/tracking-system/maro-light-tools

# optional: confirm the light controllers are reachable
python3 spots.py check

# turn the spots ON and hold (leave this terminal running)
python3 spots.py on
```

You should see `[spots] ON ... holding until Ctrl+C` and the corridor should
light up. Leave this running while you work.

> If it prints `Maro not reachable ... continuing` that is fine — it just means
> nothing else is fighting for the lights.

---

## Part B — Capture a lit still of the hallway

In a **second** terminal on the Pi:

```bash
cd /opt/tracking-system
/opt/tracking-system/.venv/bin/python tools/probe_rtsp.py \
  --config tracking_engine/config.multi_camera.yaml \
  --camera cam_hallway_n --save-stills /tmp/hallway_test
```

Expect `1/1 OK ... still=/tmp/hallway_test/cam_hallway_n.png`.

Copy that still to your **Mac/PC** (the still is already rotated correctly):

```bash
scp pi@maro-head:/tmp/hallway_test/cam_hallway_n.png ./cam_hallway_n.png
```

---

## Part C — Remove the lens distortion (on your Mac/PC)

This opens a window where you click along lines that are **physically straight**
and it computes the correction. **Run this on a machine with a screen** — not
over SSH on the Pi.

One-time setup:

```bash
pip3 install scipy opencv-python numpy pyyaml
```

Run the tool with your still:

```bash
python3 tools/calibrate_distortion_lines.py \
  --image cam_hallway_n.png --camera cam_hallway_n
```

In the window:

1. **Left-click 3 or more points** along one line you know is straight — start
   with a long **tile grout line** running down the corridor.
2. Press **`n`** to finish that line and begin the next.
3. Add several lines for a good result:
   - 2–3 tile lines running **along** the corridor,
   - 2–3 tile lines running **across** it,
   - the **LED strip**,
   - the **base of a wall**.
   More lines, in both directions, give a better fix.
4. Press **`c`** (or Enter) to compute. A **BEFORE / AFTER** preview opens —
   check that the tiles look straight in the AFTER image.
5. Press **`s`** to save. (Any other key returns to editing.)

Keys: `n` next line · `u` undo last point · `r` reset all · `c` compute ·
`s` save (in preview) · `q`/Esc quit without saving.

**Good result:** the printed `line-straightness RMS` drops to about **1 px or
less**, and the AFTER preview tiles look straight.

Saving writes the result into
`tracking_engine/calibration/camera_intrinsics.json` under `cam_hallway_n`.

---

## Part D — Send the correction to the Pi

Commit and push from your Mac/PC:

```bash
git add tracking_engine/calibration/camera_intrinsics.json
git commit -m "add cam_hallway_n lens distortion intrinsics"
git push
```

On the **Pi**, pull and reload:

```bash
cd /opt/tracking-system
git pull
SKIP_MQTT=1 sudo -E bash deploy/scripts/install.sh   # sync /opt with git
sudo systemctl restart tracking-calibrate-web tracking-engine
```

(If you prefer, you can instead `scp` the single
`tracking_engine/calibration/camera_intrinsics.json` file to the Pi.)

---

## Part E — Turn the spotlights OFF

Go back to the terminal running `spots.py on` and press **Ctrl+C**:

```
^C
```

You should see `[spots] blackout universes [0, 1]` and `[spots] done.` Confirm
the corridor goes dark. If that terminal is gone, run:

```bash
cd /opt/tracking-system/maro-light-tools
python3 spots.py off --no-maro
```

---

## Part F — Homography (later, needs Maro running)

The distortion fix above only straightens the image. To make the dot land in
the right place on the Maro floor plan, do the normal **LED + Line** calibration
once **Maro is up and reachable** (port 8420), exactly like K/WZ and Yoga:

1. Start the calibration UI on the Pi: `sudo systemctl start tracking-calibrate-web`.
2. Open it from your laptop (`ssh -L 8090:127.0.0.1:8090 pi@maro-head`, then
   `http://localhost:8090`).
3. Turn the **LED markers** on for the hallway strip.
4. Use **Line** mode: draw the corridor on the plan (east → west), then click
   the two ends of the lit LED run on the `cam_hallway_n` tile.
5. Add **one** extra point off the line (Point mode) so the result is stable.
6. Aim for the green check (residual ≤ ~5 px), then **Download session → Save**.

Full operator detail is in [`calibration_hallway.md`](calibration_hallway.md).

---

## Quick reference

| Step | Where | Command |
|------|-------|---------|
| Lights on | Pi | `python3 maro-light-tools/spots.py on` |
| Capture still | Pi | `probe_rtsp.py --camera cam_hallway_n --save-stills /tmp/hallway_test` |
| Fix distortion | Mac/PC | `python3 tools/calibrate_distortion_lines.py --image cam_hallway_n.png --camera cam_hallway_n` |
| Deploy | Mac/PC + Pi | `git push` then `git pull` + restart services |
| Lights off | Pi | Ctrl+C in the `spots.py on` terminal |
| Homography | Pi UI | `calibrate_web` LED + Line (needs Maro) |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `No module named 'scipy'` | Run `pip3 install scipy` (Part C), or on the Pi use `/opt/tracking-system/.venv/bin/python`. |
| Window doesn't open / black over SSH | The click tool needs a real screen — run it on your Mac/PC, not over SSH. |
| `pip ... no such option: --image` | You pasted two commands on one line. Run the `pip install` first, then the `python` command separately. |
| Spots don't light up | Check `ping 192.168.178.11`; make sure the ODE MK3 is powered and on the LAN. |
| AFTER preview still bowed | Add more lines (especially across the corridor) and re-compute. |
