# Tracking + Re-ID Demo

A single-file, single-camera demo of the multi-person tracking with persistent IDs
that the production system will run on the Pi 5. Built so the client can **see
the concept working today**, on a laptop with a webcam, without waiting for the
WiFi cameras to be installed or the Pi 5 to be set up.

## What this demo IS

- Person detection (YOLOv8n on CPU)
- Per-camera multi-object tracking (ByteTrack)
- Persistent global IDs (`p1`, `p2`, ...) recovered after a person walks
  off-screen and back on, via colour-histogram matching of the torso region
- Live overlay: bounding box + global ID on the camera feed
- Stylised floor-plan window: one coloured dot per global ID
- Mock JSON output to stdout in the same shape as the production HTTP POST:

  ```json
  {"cam_id":"cam_demo","ts":1714200000.123,"persons":[{"id":"p1","x":3420,"y":1870,"zone":"demo","privacy":false}]}
  ```

## What this demo is NOT

- **Not multi-camera.** Single webcam by design — the cross-camera handover and
  the global-ID gallery shared between cameras are part of the production
  `tracking_engine`, not this demo.
- **Not calibrated.** No homography. The floor-plan dot is just
  `(bbox_centre_x / frame_w, foot_y / frame_h)` — gives you a feel for "person
  is on the left side of the frame", not real mm coordinates.
- **Not Pi-grade re-ID.** Colour-histogram embedding is a 30-line stand-in for
  the OSNet x0_25 neural embedding the production system runs on the Hailo NPU.
  It will confuse two people wearing similar shirts. That's expected — the
  point of the demo is to show the *flow*, not the final accuracy.

## Run it

```bash
cd tracking_demo
pip install -r requirements.txt
python demo.py                    # uses default webcam
python demo.py --camera 1         # second webcam
python demo.py --video clip.mp4   # any video file
python demo.py --no-print         # silence the JSON stdout
```

First run downloads the YOLOv8n weights (~6 MB) automatically.
Press `q` in any window to quit.

## What you should see

- Two windows pop up: `camera (bbox + global ID)` and `floor plan`.
- A coloured box appears around each detected person, labelled `p1`, `p2`, etc.
- That same coloured circle appears in the floor-plan window at the
  approximate position the person is in the frame.
- Walk out of frame, walk back in: in most cases your label sticks (re-ID
  recovers the same `p1`). If you go far enough away or turn around, the
  histogram changes enough that you'll be assigned a new ID — that's the
  fundamental limit of histogram re-ID and exactly why the production system
  uses a learned embedding instead.
- Top-left FPS counter. On a modern laptop CPU you should see ~7-12 fps with
  one or two people in frame. Drops as more people enter.

## Privacy rooms (SZ / BZ) — not part of this demo

Optical tracking and this demo stop at the camera-tracked rooms. **SZ (bedroom)** and **BZ (bathroom)** use low-resolution thermal IR + optional water leak; fall logic is implemented in the repo root module [`thermal_fall_detection.py`](../thermal_fall_detection.py) with polygons and thresholds in [`output/cameras_config.json`](../output/cameras_config.json). The production HTTP API can emit `fall` events from that path in parallel with camera `persons` POSTs.

## How this maps to the production system

| Demo                                   | Production (`tracking_engine/` on Pi 5 + Hailo)             |
|----------------------------------------|-------------------------------------------------------------|
| Single webcam                          | 6 RTSP streams ingested in parallel                         |
| YOLOv8n on CPU (~7-12 fps)             | YOLOv8n on Hailo NPU (~200+ fps headroom)                   |
| Colour-histogram torso embedding       | OSNet x0_25 neural embedding on Hailo                       |
| In-process gallery dict                | Cross-camera gallery with spatial gating                    |
| `(bbox_x / w, foot_y / h)` floor pos   | 4-point per-camera homography -> mm in floor-plan frame     |
| `print(json.dumps(...))`               | HTTP POST to Maro / FastAPI                                 |
| Single "demo" zone                     | Privacy filter: SZ etc. replaced with zone centroid         |
| (not in demo)                         | Thermal fall: `thermal_fall_detection.py` + MQTT / `POST /events` |

The demo deliberately keeps the same JSON shape as the production POST so the
Maro server side can be developed against the demo's stdout *now*, instead of
waiting for the full system.
