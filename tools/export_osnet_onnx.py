#!/usr/bin/env python3
"""Export OSNet-x0.25 from torchreid to ONNX (FP32) and optionally INT8-quantise.

Output is consumed by ``tracking_engine.reid.embed.OnnxBodyEmbedder`` when
``reid.body.onnx_model_path`` points at the produced file.

Usage (run on a development machine with torch + torchreid available):

    python tools/export_osnet_onnx.py \
        --out tracking_engine/models/osnet_x025.onnx \
        --quantize tracking_engine/models/osnet_x025_int8.onnx

Notes
-----
- Input  : float32 BCHW, RGB 0..1, default size 256 x 128.
- Output : 512-D feature vector (raw embedding; L2 normalisation is done at
  runtime by ``OnnxBodyEmbedder``).
- INT8 quantisation uses ``onnxruntime.quantization.quantize_dynamic`` —
  works without calibration data, fast on Raspberry Pi 5 CPU.

Sanity self-test
----------------
When run with ``--self-test`` plus ``--sample-dir`` of person crops, the
script prints a small cosine-distance matrix so you can confirm that two
different people produce distinguishable embeddings (intra ~ 0.0-0.2,
inter > 0.4 is healthy).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _export(out_path: Path, height: int, width: int, opset: int) -> None:
    try:
        import torch
        import torchreid
    except ImportError as exc:
        raise SystemExit(
            "torch / torchreid are required for export. Install with:\n"
            "  pip install torch torchreid onnx onnxsim\n"
            f"(import failed: {exc})"
        ) from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)

    model = torchreid.models.build_model(
        name="osnet_x0_25",
        num_classes=1,
        loss="softmax",
        pretrained=True,
    )
    model.eval()

    dummy = torch.randn(1, 3, height, width, dtype=torch.float32)

    print(f"[export] tracing osnet_x0_25 -> {out_path} (HxW={height}x{width}, opset={opset})")
    torch.onnx.export(
        model,
        dummy,
        str(out_path),
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=opset,
        do_constant_folding=True,
    )

    try:
        import onnx
        import onnxsim

        m = onnx.load(str(out_path))
        m_simple, ok = onnxsim.simplify(m)
        if ok:
            onnx.save(m_simple, str(out_path))
            print(f"[export] simplified graph written: {out_path}")
        else:
            print("[export] onnxsim returned ok=False; keeping un-simplified graph", file=sys.stderr)
    except ImportError:
        print("[export] onnx / onnxsim not installed; skipping graph simplification", file=sys.stderr)


def _quantize(src_path: Path, dst_path: Path) -> None:
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
    except ImportError as exc:
        raise SystemExit(
            "onnxruntime is required for INT8 quantisation. Install with:\n"
            "  pip install onnxruntime\n"
            f"(import failed: {exc})"
        ) from exc

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[quant] dynamic INT8 -> {dst_path}")
    quantize_dynamic(
        model_input=str(src_path),
        model_output=str(dst_path),
        weight_type=QuantType.QInt8,
    )


def _self_test(onnx_path: Path, sample_dir: Path, height: int, width: int) -> None:
    try:
        import cv2
        import numpy as np
        import onnxruntime as ort
    except ImportError as exc:
        raise SystemExit(f"self-test deps missing: {exc}") from exc

    if not sample_dir.is_dir():
        raise SystemExit(f"self-test sample dir does not exist: {sample_dir}")

    paths = sorted(
        p for p in sample_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
    )
    if len(paths) < 2:
        raise SystemExit("need at least 2 person crops in --sample-dir for self-test")

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name

    embeddings: list[np.ndarray] = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            print(f"[self-test] skip unreadable {p}", file=sys.stderr)
            continue
        img = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        chw = np.transpose(rgb, (2, 0, 1))[None, ...]
        out = sess.run(None, {input_name: chw})[0]
        v = out.reshape(-1).astype(np.float32)
        v /= np.linalg.norm(v) + 1e-12
        embeddings.append(v)

    n = len(embeddings)
    print("[self-test] pairwise cosine distance:")
    header = ["      "] + [f"{paths[j].name[:8]:>10s}" for j in range(n)]
    print("".join(header))
    for i in range(n):
        row = [f"{paths[i].name[:8]:<10s}"]
        for j in range(n):
            cs = float(np.dot(embeddings[i], embeddings[j]))
            row.append(f"{(1.0 - cs):>10.3f}")
        print("".join(row))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True, help="FP32 ONNX output path")
    ap.add_argument("--quantize", type=Path, default=None, help="optional INT8 ONNX output path")
    ap.add_argument("--height", type=int, default=256)
    ap.add_argument("--width", type=int, default=128)
    ap.add_argument("--opset", type=int, default=14)
    ap.add_argument("--self-test", action="store_true", help="run pairwise cosine sanity test")
    ap.add_argument("--sample-dir", type=Path, default=None, help="dir of person crops for self-test")
    args = ap.parse_args()

    if not args.out.parent.exists():
        args.out.parent.mkdir(parents=True, exist_ok=True)

    if not args.out.exists():
        _export(args.out, args.height, args.width, args.opset)
    else:
        print(f"[export] {args.out} already exists, skipping FP32 export")

    if args.quantize is not None:
        if args.quantize.exists():
            print(f"[quant] {args.quantize} already exists, skipping quantisation")
        else:
            _quantize(args.out, args.quantize)

    if args.self_test:
        target = args.quantize if (args.quantize is not None and args.quantize.exists()) else args.out
        if args.sample_dir is None:
            raise SystemExit("--self-test requires --sample-dir DIR")
        _self_test(target, args.sample_dir, args.height, args.width)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
