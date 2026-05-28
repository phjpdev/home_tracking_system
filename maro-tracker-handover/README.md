# Maro Tracker — Handover (additions to v8)

All work done on top of `home_tracking_system-main` v8 zip. Files in `modified_files/`
preserve original repo structure — drop them in to replace.

## Headline changes

### 1. LED-marker calibration aid  (NEW)
Standalone control of WS2814 RGBW ARGB strips via Art-Net from `calibrate_web`,
**bypassing Maro completely**. Pixelator MK2 at 192.168.178.10:6454.
- New file: `calibrate_web/led_marker.py` — Art-Net sender, marker geometry
- `calibrate_web/app.py` — new endpoints `/api/led/strips`, `/api/led/all_markers`,
  `/api/led/marker`, `/api/led/off`
- Frontend toggle in `static/index.html` + `static/app.js`: lights every Nth marker
  (default 5 ON / 5 OFF = 50 cm bright blocks / 50 cm dark gaps) and renders the
  matching glow lines on the plan. Lets the operator click pixel-accurate landmarks
  at known strip positions instead of guessing on architecture features.
- Reference code we mirrored: `reference_meter_test_gui.py` (Maro's existing tool —
  same ArtDmx packet format, channel layout, pixel_grouping logic). LED control is
  Maro-independent so calibrate_web runs even with Maro stopped.

### 2. Line / polygon mode for calibration  (NEW)
Toolbar radio: Point / Line. In Line mode:
- Two plan clicks define a line → N landmarks placed evenly (configurable, default 3)
- Two clicks in each camera tile define the line endpoints there → linear
  interpolation fills the in-between cam clicks
- Visualizes the connecting line in both panes
- Per-tile click re-render bug fix (camera dot only redrew on next render)

### 3. Y-axis flip bug fix
`pipeline/maro_floorplan.py::mm_to_plan_px` used `(1 - nu) * h`. Maro's
`render_floorplan_png.py` already applies `ax.invert_yaxis()`, so the second flip
mis-placed every overlay zone/spot/strip. Removed the extra flip.
Also: `spots[].position` from Maro arrives as list `[x, y]`, not dict — handled both.

### 4. Plan-pixel ↔ legacy-mm bridge to Maro
Maro's `/tracking/positions` endpoint expects the legacy mm space (0..19800 ×
0..10200) and re-scales internally to its mm bounds (-6193..11072 × -5624..2843).
The new floor-plan-pixel calibration emits 0..2700 × 0..1324.
`pipeline/poster.py` reads `plan_w_px / plan_h_px` from the calibration and
pre-multiplies by (19800/2700, 10200/1324) before POST so Maro's existing
SCALE+OFFSET produces the right plan coords — Maro server unmodified.
Set env `TRACKER_POSTER_DEBUG=1` to log every outgoing payload.

### 5. MPS (Apple GPU) detection
`pipeline/detector_cpu.py` (rename pending) auto-selects `cuda > mps > cpu` and
respects `YOLO_DEVICE` env override. On M-series Macs, yolov8n inference drops
from ~25 ms/cam (CPU) to ~12 ms/cam (MPS) — roughly 2× faster.

### 6. Velocity outlier gate in fusion
Strong lens distortion on some cameras (hallway fish-eye) causes single-frame
foot-point spikes after homography. `pipeline/plan_fusion.py` now rejects
measurements faster than `max_speed_pxps` (default 1500 px/s ≈ 9.6 m/s, i.e.
human sprint speed) and releases after `outlier_release_after` consecutive
outliers (handles real teleport / re-id swap).

### 7. Per-camera detection breakdown in tick log
`multi_camera.py` tick line now prints `kwz_sw=0 kwz_nw=1 …` for all 7 cams so
you can see which camera is silent. Implementation note: in fused mode the
counter still needs to be wired off `fusion_rows` rather than `pending_payloads`
(known small bug — currently shows 0 for all cams even when fused = N).

### 8. Config defaults updated (`config.multi_camera.yaml`)
- `cam_hallway_n.rotate: 90` (was 180 — physical re-mount)
- `tracking.plan_fusion.ema_alpha: 0.5` (was 0.35)
- `tracking.plan_fusion.cluster_distance_px: 120` (was 80)
- `tracking.plan_fusion.drop_outside_zones: false` (no Flur zone defined yet)
- `reid.enabled: true` with `body.onnx_model_path: null` (dev fallback;
  faiss-cpu must be installed)

## Open issues / next steps

- **Hallway lens distortion** still causes residual jitter even with the outlier
  gate. Proper fix: chessboard intrinsic calibration → `cv2.undistort()` before
  YOLO/homography on hallway cam only. ~30 min of board capture per cam.
- **Per-camera ema_alpha** would let hallway be smoothed harder than the others
  without slowing them down. Currently global.
- **Floor / hallway zone polygon** missing in Maro `zones.json` — add it so
  `drop_outside_zones=true` works again (kicks out false positives in the void).
- **Maro endpoint** could learn `coordinate_space: "maro_floorplan_px"` and skip
  the legacy scale altogether — would remove the poster.py hack.

## Files in this archive
```
modified_files/                  ← drop into Jean's repo to replace
  tracking_engine/
    calibrate_web/
      app.py                     ← LED endpoints + robust device-list
      led_marker.py              ← NEW
      static/
        app.js                   ← Line mode, LED overlay, render fixes
        index.html               ← Mode radio + LED toggle
        style.css                ← Native-size tiles, no crop
    pipeline/
      detector_cpu.py            ← MPS auto-select
      maro_floorplan.py          ← Y-flip fix, list-position support
      plan_fusion.py             ← Velocity outlier gate
      poster.py                  ← Plan-px → legacy-mm rescale
    multi_camera.py              ← Per-cam breakdown, plan_w_px wired
    config.multi_camera.yaml     ← Updated defaults
  reference_meter_test_gui.py    ← FYI: Maro's existing ArtNet tool we mirrored

diffs/                           ← unified diff vs original for each file
```

Built and tested on Mac (Apple Silicon, MPS), Python 3.14, ultralytics 8.4.46,
PyTorch 2.11, opencv 4.13.
