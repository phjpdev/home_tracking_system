# tracking_engine/models/

Local ONNX model store. The repository ships **without** the binaries to
keep the working tree small; they must be exported once per machine.

| File | Purpose | Build with |
|------|---------|------------|
| `osnet_x025.onnx` | OSNet-x0.25 body Re-ID embedder, FP32, 512-D | `python tools/export_osnet_onnx.py --out tracking_engine/models/osnet_x025.onnx` |
| `osnet_x025_int8.onnx` | INT8-quantised variant (Pi 5 default) | `python tools/export_osnet_onnx.py --out tracking_engine/models/osnet_x025.onnx --quantize tracking_engine/models/osnet_x025_int8.onnx` |
| `scrfd_500mf.onnx` | SCRFD face detector (Phase D) | Download from `insightface` model zoo |
| `arcface_mfn.onnx` | ArcFace MobileFaceNet 128-D embedder (Phase D) | Download from `insightface` model zoo |

After dropping the files here, point `reid.body.onnx_model_path` (and
`reid.face.detector_onnx_path` / `reid.face.embedder_onnx_path`) in
[../config.multi_camera.yaml](../config.multi_camera.yaml) at the
chosen variants.
