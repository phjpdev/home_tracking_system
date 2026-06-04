# Maro floor-plan pixel calibration

Calibrate all cameras into **Maro’s** floor-plan image space (2700×1324 px), not architect mm.
Landmarks (door corners, lamps, strips) replace “stand here” positions. Works **remotely**
with saved stills; no one needs to walk the house during calibration.

## Coordinate contract

| Field | Meaning |
|-------|---------|
| `camera_calibrations.json` → `_coordinate_space` | `"maro_floorplan_px"` |
| `world_points_plan_px` | Landmark position on Maro plan (full image pixels) |
| `H` | 3×3 homography: camera (u,v) → (plan_x, plan_y) |
| Live POST `persons[].x`, `persons[].y` | **Plan pixels** (when calibrated + `poster.fused_mode`) |

Maro must draw dots at these coordinates on `floorplan_bg.png` without extra conversion.

## Prerequisites

- Maro running on the Pi (`http://127.0.0.1:8420` for services; UI at `http://<pi-lan-ip>:8420/`).
- `fastapi`, `uvicorn`, `requests` installed (`tracking_engine/requirements.txt`).
- Seven camera stills or live RTSP from `config.multi_camera.yaml`.

## 1. Cache Maro assets (once per machine)

On the Pi, use the **installed** tree (not `~/home_tracking_system` unless you run from there
with the venv). Maro must be listening on port **8420**:

```bash
cd /opt/tracking-system
CACHE=/opt/tracking-system/tracking_engine/calibration/maro_cache
mkdir -p "$CACHE"
curl -sf http://127.0.0.1:8420/api/floorplan -o "$CACHE/floorplan.json"
curl -sf http://127.0.0.1:8420/api/floorplan/bg -o "$CACHE/floorplan_bg.png"
curl -sf http://127.0.0.1:8420/api/zones -o "$CACHE/zones.json"
curl -sf http://127.0.0.1:8420/api/spots -o "$CACHE/spots.json"
curl -sf http://127.0.0.1:8420/api/strips -o "$CACHE/strips.json"
ls -la "$CACHE"
```

If any `curl` fails, check Maro is up: `curl -sI http://127.0.0.1:8420/api/floorplan`.

`calibrate_web` refreshes this cache automatically when the API is reachable.

## 2. Start calibration UI

**Do not use system `python` / `pip`** on the Pi (PEP 668 blocks it). Use the install venv
at `/opt/tracking-system/.venv` (created by `deploy/scripts/install.sh`).

**Recommended — systemd** (survives logout, runs as `tracking` user):

```bash
sudo systemctl enable --now tracking-calibrate-web
sudo systemctl status tracking-calibrate-web
```

**One-off in foreground** (debugging):

```bash
cd /opt/tracking-system
export TRACKING_CONFIG=/opt/tracking-system/tracking_engine/config.multi_camera.yaml
/opt/tracking-system/.venv/bin/python -m tracking_engine.calibrate_web --host 0.0.0.0 --port 8090
```

From a git checkout under `~/home_tracking_system`, same idea:

```bash
cd ~/home_tracking_system
export TRACKING_CONFIG=tracking_engine/config.multi_camera.yaml
/opt/tracking-system/.venv/bin/python -m tracking_engine.calibrate_web --host 0.0.0.0 --port 8090
```

**From your laptop via SSH port-forward:**

```bash
ssh -L 8090:127.0.0.1:8090 maro@maro-head.tail79e96b.ts.net
# on Pi in another session:
python -m tracking_engine.calibrate_web
```

Open `http://localhost:8090`.

**Off-site with stills:**

```powershell
python -m tracking_engine.calibrate_web `
  --maro-api http://127.0.0.1:8420 `
  --video cam_kwz_sw=stills/cam_kwz_sw.png `
  ... (all seven cameras)
```

## 3. Calibrate (per camera)

1. **Recapture all** if using live RTSP (skipped for static PNG overrides).
2. Click a landmark on the **Maro plan** (zones, spots, strips visible).
3. In each camera tile that sees that landmark, click the **same feature**.
4. Repeat **6–8 landmarks per camera**, spread across the field of view.
5. Watch per-camera **mean residual** (target ≤ 5 px, green ✓).
6. **Download session** as backup, then **Save**.

Acceptance at save time: mean residual ≤ 8 px per camera (configurable); force override available.

## 4. Enable live tracking

Ensure [`config.multi_camera.yaml`](../tracking_engine/config.multi_camera.yaml) has:

```yaml
maro:
  api_base: http://127.0.0.1:8420
calibration:
  coordinate_space: maro_floorplan_px
tracking:
  plan_fusion:
    enabled: true
poster:
  fused_mode: true
```

Restart:

```bash
sudo systemctl restart tracking-engine
```

## 5. Acceptance test (~10 px on Maro)

Walk each zone; the live dot on Maro’s floor plan should stay within **~10 px** of your true position.
If multiple ghost dots appear, confirm `poster.fused_mode: true` (one fused POST per tick).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Startup: “could not load Maro floor plan” | Run §1 curl cache; check Maro on :8420 |
| Dots far from plan | Recalibrate — old mm calibrations are incompatible |
| 2–4 offset dots, same person | Enable `plan_fusion` + `fused_mode` |
| Yoga/hallway upside-down in UI only | Use `probe_rtsp` stills (pre-rotated); don’t double-rotate PNG overrides |
| Residual always high on one cam | Re-pick landmarks; avoid colinear points; use lamp corners |

## Hallway camera

Narrow corridor + sideways mount + wide-angle lens: see
[`calibration_hallway.md`](calibration_hallway.md) (undistort, LED, Line mode).

## Legacy tools

- [`tools/calibrate_homography.py`](../tools/calibrate_homography.py) — single-camera mm (architect plan).
- Walk-based [`calibration_day_handover.md`](calibration_day_handover.md) — optional on-site validation only.
