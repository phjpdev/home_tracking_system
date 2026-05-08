# Tracking engine (Phase C MVP)

Single-camera pipeline aligned with the project plan:

**RTSP** (OpenCV + FFmpeg) → **YOLOv8n** (Hailo on Pi when available, else Ultralytics CPU) → **ByteTrack** → **foot-point → floor mm** (dummy linear map or `cv2`-style homography matrix) → **HTTP POST** JSON (same shape as `tracking_demo`).

Per-frame timings go to stderr (`grab`, `detect`, `track`, `geom`, `post`, `frame_total`).

## Install

From the repository root (`Tracking_System/`):

```bash
pip install -r tracking_engine/requirements.txt
```

On the Raspberry Pi with the Hailo HAT, install the vendor stack (HailoRT, `picamera2`, and a packaged YOLO HEF, e.g. `/usr/share/hailo-models/yolov8s_h8l.hef`) per Raspberry Pi / Hailo documentation. This repo only wires inference through `picamera2.devices.Hailo` when `backend` is `hailo` or `auto` and the HEF path exists.

## Configure

Edit [`config.yaml`](config.yaml):

| Block | Purpose |
|--------|--------|
| `camera.id` | Must match a key in [`calibration/camera_calibrations.json`](calibration/camera_calibrations.json). |
| `camera.rtsp_url` | Sub stream (e.g. 640×480 / H.264) is enough for the MVP and keeps latency down. |
| `detector.backend` | `auto` (try Hailo, else CPU), `cpu`, or `hailo`. |
| `detector.hailo_hef` | Path to the compiled YOLO HEF on the Pi. |
| `poster.url` | Maro endpoint; default matches the mock server. |
| `poster.dry_run` | If `true`, no HTTP traffic — payloads are not printed here (use mock server to see JSON). |

Calibration modes in `camera_calibrations.json`:

- **`dummy`** — `floor_bounds_mm` + normalized image coordinates map linearly to mm (placeholder until the web cal tool exists).
- **`homography`** — `H` is a 3×3 matrix mapping the foot pixel (u, v) to (x_mm, y_mm) on the plan. Optional `calib_image_width` / `calib_image_height` scale points if the live stream resolution differs from calibration.

## How to test

### A. Laptop / CI (no camera)

Use a sample video and CPU detection.

1. Set in `config.yaml`: `poster.dry_run: true` **or** start the mock server (step 2).
2. Optional: terminal B — `python tracking_engine/mock_maro_server.py`  
   Listens on `http://0.0.0.0:8765/tracking/positions` and prints each JSON line to stdout.
3. Terminal A — from repo root:

   ```bash
   python -m tracking_engine --config tracking_engine/config.yaml --video path/to/people.mp4
   ```

4. Watch stderr for lines like  
   `grab=... detect=... track=... geom=... post=... frame_total=...`  
   and, if the mock server is running, POST bodies on terminal B.

5. Limit run length by setting `runtime.max_frames` in `config.yaml` (e.g. `300`).

### B. Real RTSP camera (same LAN)

1. Put the camera’s RTSP URL in `camera.rtsp_url` (codec and path depend on firmware; many boards expose a “sub” stream for lower latency).
2. Ensure the PC/Pi can reach that URL: `ffplay` / VLC smoke test.
3. `python -m tracking_engine --config tracking_engine/config.yaml`
4. Tune `detector.conf` and ByteTrack later if IDs flicker.

### C. Raspberry Pi 5 + Hailo

1. Install Hailo + `picamera2` packages and confirm a HEF path (update `detector.hailo_hef`).
2. Set `detector.backend: auto` or `hailo`.
3. Run the same `python -m tracking_engine ...` command from the repo on the Pi (RTSP ingest does not require the Pi Camera Module).

### D. Latency in the POST body

Set `poster.include_latency_in_post: true` if the server should receive a `latency_ms` object (optional; Maro should tolerate extra keys).

## Run entrypoint

Always run as a package from the repo root so imports resolve:

```bash
python -m tracking_engine --help
```

## JSON payload shape

Same as `tracking_demo`: `cam_id`, `ts`, `persons[]` with `id` (`t<track_id>` from ByteTrack for this MVP), `x` / `y` in mm, `zone`, `privacy`.

## Note on supervision

Current supervision still ships `ByteTrack`; the library shows a deprecation warning. When upstream removes it, swap this module for their recommended tracker in `tracker_bytetrack.py` only.
