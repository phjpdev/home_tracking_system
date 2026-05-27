# Calibration — operator guide

**Primary method (recommended):** landmark-based calibration on the **Maro floor plan**
(plan pixels, same canvas as the Maro UI). No walking required — works remotely with
camera stills. Full steps: [`calibration_maro_plan_px.md`](calibration_maro_plan_px.md).

**Legacy / optional:** walk-and-stand calibration on the architect placement plan (mm).
That path does **not** align dots on Maro; use only if Maro plan-pixel calibration is
unavailable.

---

## Maro landmark calibration (15–20 min, remote OK)

### What you need

- Raspberry Pi on and reachable (Tailscale or house Wi‑Fi).
- Maro UI/API on port **8420** (same machine or LAN).
- Seven cameras online **or** seven PNG stills under `stills/` (installer can provide).
- A laptop/phone browser; SSH port-forward is fine.

### Start the tool

On the Pi:

```bash
python -m tracking_engine.calibrate_web --host 0.0.0.0 --port 8090
```

From your laptop (forward port 8090):

```bash
ssh -L 8090:127.0.0.1:8090 maro@maro-head.tail79e96b.ts.net
```

Open `http://localhost:8090`. You should see the **Maro** floor plan (dark background,
zones, lamps, strips) and seven camera tiles.

### The loop

1. Pick a **fixed landmark** (door corner, lamp base, strip corner) visible on the plan.
2. Click that point on the **floor plan** (left panel). A magnifier helps precision.
3. In every camera tile that sees the same feature, click it once.
4. Add **6–8 landmarks per camera**, spread across the field of view (not all in a line).
5. Watch each tile: **green ✓** when mean residual ≤ 5 px and ≥ 6 points.
6. **Download session** (backup), then **Save**.

Restart tracking after save:

```bash
sudo systemctl restart tracking-engine
```

### Acceptance

Walk the house; the live dot on Maro should stay within **~10 px** of your true position.
If you see several offset dots for one person, confirm `poster.fused_mode: true` in config
(see [`calibration_maro_plan_px.md`](calibration_maro_plan_px.md)).

### When to recalibrate

- A camera bracket moved.
- Landmarks you used were removed (furniture, tape).
- Dots are consistently wrong on Maro.

---

## Legacy: walk-and-stand (architect mm plan)

> Deprecated for Maro live view. Kept for on-site validation or old mm calibrations.

You walk the house, stand at each spot, tap the position on the **architect** floor plan,
and tap your feet in each camera. Residuals are in **mm**; save rejects mean > 250 mm
unless forced. This does **not** place dots correctly on Maro until you re-calibrate with
the Maro plan workflow above.

For the full legacy walk-through (positions per room, colour codes in mm), ask the installer
for the previous version of this doc or use [`tools/calibrate_homography.py`](../tools/calibrate_homography.py)
for a single camera in mm.
