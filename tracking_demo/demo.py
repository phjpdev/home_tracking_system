"""
Multi-Person Tracking + Re-ID Demo  (CPU-only, single camera)
==============================================================

Goal: demonstrate end-to-end tracking and re-ID on one camera before the
full multi-camera Pi deployment is available.

What it does, per frame:
    1. read a frame from a webcam or video file
    2. detect persons with YOLOv8n on CPU
    3. update a per-camera ByteTrack tracker
    4. compute a colour-histogram embedding of each person's torso
    5. match that embedding against a small in-memory "gallery"
       to recover a persistent global ID (`p1`, `p2`, ...) even
       across short disappearances
    6. draw bbox + global-ID label on the live frame
    7. draw a stylised floor-plan view with one coloured dot per ID
    8. print one JSON line per frame that mimics the format the
       production tracking engine will POST to the Maro server

What it is NOT:
    - production-accurate. Histogram re-ID is the cheapest possible
      proxy for the OSNet embedding the production system will use.
    - calibrated. There is no homography here; the floor-plan dot
      position is just `(bbox_centre_x / w, foot_y / h)`. The full
      tracking engine on the Pi will use a per-camera homography
      computed by the calibration tool.
    - multi-camera. This is single-camera by design — kept minimal so it
      runs easily on a developer laptop with a webcam.

USAGE
-----
    python demo.py                       # webcam (index 0)
    python demo.py --camera 1            # second webcam
    python demo.py --video clip.mp4      # a recorded video
    python demo.py --no-print            # silence stdout JSON

Press 'q' in any window to quit.

REQUIREMENTS
------------
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

PERSON_CLASS_ID = 0
DETECT_CONF = 0.40
REID_THRESHOLD = 0.65    # cosine similarity to claim "same person"
GALLERY_TTL_SEC = 60.0   # forget a global ID after this many seconds idle
HIST_BINS = (8, 8)       # H x S bins of HSV histogram


# ---------------------------------------------------------------------------
# Re-ID: torso colour-histogram embedding + cosine similarity
# ---------------------------------------------------------------------------

def torso_histogram(frame: np.ndarray, bbox) -> Optional[np.ndarray]:
    """Crop the torso region (top 20-60% of bbox) and compute an HSV
    H-S histogram. Returns a flat float32 vector or None if invalid.
    """
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = y2 - y1, x2 - x1
    if h <= 4 or w <= 4:
        return None
    ty1 = max(0, y1 + int(h * 0.20))
    ty2 = max(0, y1 + int(h * 0.60))
    tx1 = max(0, x1)
    tx2 = max(0, x2)
    crop = frame[ty1:ty2, tx1:tx2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, list(HIST_BINS),
                        [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().astype(np.float32)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class GlobalIdGallery:
    """Maps short-lived ByteTrack IDs to persistent global IDs.

    The production system replaces this with an OSNet embedding
    gallery (Hailo NPU); the API stays the same.
    """

    def __init__(self, threshold: float = REID_THRESHOLD,
                 ttl: float = GALLERY_TTL_SEC,
                 max_history: int = 12):
        self.threshold = threshold
        self.ttl = ttl
        self.max_history = max_history
        self._next_global = 1
        self._gallery: dict[str, dict] = {}
        self._track_to_global: dict[int, str] = {}

    def _new_global(self) -> str:
        gid = f"p{self._next_global}"
        self._next_global += 1
        self._gallery[gid] = {
            "hists": deque(maxlen=self.max_history),
            "last_seen": 0.0,
        }
        return gid

    def _purge(self, now: float) -> None:
        for gid in [g for g, e in self._gallery.items()
                    if now - e["last_seen"] > self.ttl]:
            del self._gallery[gid]
        self._track_to_global = {
            t: g for t, g in self._track_to_global.items()
            if g in self._gallery
        }

    def assign(self, track_id: int, hist: Optional[np.ndarray],
               now: float) -> str:
        if track_id in self._track_to_global:
            gid = self._track_to_global[track_id]
            entry = self._gallery.get(gid)
            if entry is not None:
                if hist is not None:
                    entry["hists"].append(hist)
                entry["last_seen"] = now
                return gid

        self._purge(now)

        if hist is None:
            gid = self._new_global()
            self._gallery[gid]["last_seen"] = now
            self._track_to_global[track_id] = gid
            return gid

        best_gid: Optional[str] = None
        best_score = self.threshold
        for gid, entry in self._gallery.items():
            for h in entry["hists"]:
                score = cosine_sim(hist, h)
                if score > best_score:
                    best_score = score
                    best_gid = gid
                    break

        if best_gid is None:
            best_gid = self._new_global()

        self._gallery[best_gid]["hists"].append(hist)
        self._gallery[best_gid]["last_seen"] = now
        self._track_to_global[track_id] = best_gid
        return best_gid

    def __len__(self) -> int:
        return len(self._gallery)


# ---------------------------------------------------------------------------
# Visual rendering
# ---------------------------------------------------------------------------

PALETTE = [
    (208, 119,  31), (14,  127, 255), ( 44, 160,  44), ( 40,  39, 214),
    (189, 103, 148), ( 75,  86, 140), (194, 119, 227), (127, 127, 127),
]


def color_for(gid: str) -> tuple[int, int, int]:
    n = int(gid.lstrip("p")) if gid.startswith("p") else 1
    return PALETTE[(n - 1) % len(PALETTE)]


def draw_box(img: np.ndarray, bbox, label: str,
             color: tuple[int, int, int]) -> None:
    x1, y1, x2, y2 = [int(v) for v in bbox]
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    text_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
    bx1, by1 = x1, max(0, y1 - text_size[1] - 10)
    bx2, by2 = x1 + text_size[0] + 12, y1
    cv2.rectangle(img, (bx1, by1), (bx2, by2), color, -1)
    cv2.putText(img, label, (bx1 + 6, by2 - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)


def render_floor_plan(positions: dict[str, tuple[float, float]],
                      size: tuple[int, int] = (640, 320)) -> np.ndarray:
    canvas = np.full((size[1], size[0], 3), 245, dtype=np.uint8)
    for x in range(0, size[0], 64):
        cv2.line(canvas, (x, 0), (x, size[1]), (220, 220, 220), 1)
    for y in range(0, size[1], 64):
        cv2.line(canvas, (0, y), (size[0], y), (220, 220, 220), 1)
    cv2.rectangle(canvas, (4, 4), (size[0] - 4, size[1] - 4), (60, 60, 60), 2)
    cv2.putText(canvas, "Floor plan view  (demo, no homography)",
                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (60, 60, 60), 2)
    margin = 40
    for gid, (nx, ny) in positions.items():
        x = int(nx * (size[0] - 2 * margin) + margin)
        y = int(ny * (size[1] - 2 * margin) + margin)
        color = color_for(gid)
        cv2.circle(canvas, (x, y), 16, color, -1)
        cv2.circle(canvas, (x, y), 16, (255, 255, 255), 2)
        text_size = cv2.getTextSize(gid, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)[0]
        cv2.putText(canvas, gid,
                    (x - text_size[0] // 2, y + text_size[1] // 2 - 1),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return canvas


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def open_capture(args) -> cv2.VideoCapture:
    if args.video:
        return cv2.VideoCapture(args.video)
    if sys.platform == "win32":
        return cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    return cv2.VideoCapture(args.camera)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="path to a video file (default: webcam)")
    ap.add_argument("--camera", type=int, default=0, help="webcam index")
    ap.add_argument("--model", default="yolov8n.pt", help="YOLO model weight")
    ap.add_argument("--no-print", action="store_true",
                    help="suppress per-frame JSON to stdout")
    args = ap.parse_args()

    cap = open_capture(args)
    if not cap.isOpened():
        print(f"[demo] could not open video source: "
              f"{args.video or args.camera}", file=sys.stderr)
        return 1

    # Imported lazily so the script can be linted without ultralytics installed.
    print("[demo] loading YOLO model (first run downloads ~6 MB) ...",
          file=sys.stderr)
    from ultralytics import YOLO
    import supervision as sv

    model = YOLO(args.model)
    tracker = sv.ByteTrack()
    gallery = GlobalIdGallery()

    frame_count = 0
    t_start = time.time()
    print("[demo] running. press 'q' in a window to quit.", file=sys.stderr)

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[demo] end of stream", file=sys.stderr)
            break

        results = model(frame, classes=[PERSON_CLASS_ID],
                        conf=DETECT_CONF, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)
        detections = tracker.update_with_detections(detections)

        ts = time.time()
        positions: dict[str, tuple[float, float]] = {}
        for i in range(len(detections)):
            track_id = detections.tracker_id[i] if detections.tracker_id is not None else None
            xyxy = detections.xyxy[i]
            if track_id is None:
                draw_box(frame, xyxy, "?", (128, 128, 128))
                continue
            hist = torso_histogram(frame, xyxy)
            gid = gallery.assign(int(track_id), hist, ts)

            x1, y1, x2, y2 = xyxy
            cx = (x1 + x2) / 2.0
            foot_y = y2
            nx = float(np.clip(cx / frame.shape[1], 0.0, 1.0))
            ny = float(np.clip(foot_y / frame.shape[0], 0.0, 1.0))
            positions[gid] = (nx, ny)

            draw_box(frame, xyxy, gid, color_for(gid))

        floor = render_floor_plan(positions)

        frame_count += 1
        elapsed = time.time() - t_start
        fps = frame_count / elapsed if elapsed > 0 else 0
        cv2.putText(frame,
                    f"{fps:.1f} fps   active: {len(positions)}   "
                    f"global IDs: {len(gallery)}",
                    (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (0, 200, 0), 2)

        cv2.imshow("camera (bbox + global ID)", frame)
        cv2.imshow("floor plan", floor)

        if positions and not args.no_print:
            payload = {
                "cam_id": "cam_demo",
                "ts": round(ts, 3),
                "persons": [
                    {
                        "id": gid,
                        # mock floor mm coords scaled to a 10x5 m room
                        # (real system uses the per-camera homography)
                        "x": int(nx * 10000),
                        "y": int(ny * 5000),
                        "zone": "demo",
                        "privacy": False,
                    }
                    for gid, (nx, ny) in positions.items()
                ],
            }
            print(json.dumps(payload), flush=True)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
