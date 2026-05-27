"""Atomic merge into ``camera_calibrations.json``."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..pipeline.maro_floorplan import COORDINATE_SPACE

DEFAULT_CALIB_PATH = Path("tracking_engine/calibration/camera_calibrations.json")


def read_calibrations(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_merge_calibrations(
    path: Path,
    new_entries: dict[str, dict[str, Any]],
    *,
    document_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = read_calibrations(path)
    if document_meta:
        for key, val in document_meta.items():
            existing[key] = val
    existing.update(new_entries)

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(existing, indent=2, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(serialized)
        tmp.flush()
        try:
            os.fsync(tmp.fileno())
        except OSError:
            pass
        tmp_name = tmp.name

    os.replace(tmp_name, str(path))
    return existing


def build_camera_entry(
    *,
    fit: Any,
    img_w: int,
    img_h: int,
    plan_bounds_px: dict[str, float],
    calibrated_at: str,
) -> dict[str, Any]:
    image_points = [list(uv) for uv in fit.image_points]
    world_points = [list(xy) for xy in fit.world_points]
    return {
        "mode": "homography",
        "coordinate_space": COORDINATE_SPACE,
        "plan_bounds_px": plan_bounds_px,
        "H": [[float(v) for v in row] for row in fit.H.tolist()],
        "calib_image_width": int(img_w),
        "calib_image_height": int(img_h),
        "calibrated_at": calibrated_at,
        "image_points": [[int(round(u)), int(round(v))] for u, v in image_points],
        "world_points_plan_px": [[float(x), float(y)] for x, y in world_points],
        "_comment": (
            "Maro floor-plan pixels via calibrate_web. "
            f"residual mean={fit.residual_px_mean:.1f}px max={fit.residual_px_max:.1f}px "
            f"n={fit.num_points}."
        ),
    }
