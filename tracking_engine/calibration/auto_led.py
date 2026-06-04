"""LED marker sequence calibration: Art-Net markers → camera blobs → homography."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from ..calibrate_web.homography_fit import CameraFit, _fit_one
from ..calibrate_web.led_marker import LedMarker
from ..pipeline.maro_floorplan import MaroFloorplanAssets, load_maro_floorplan


@dataclass
class LedAutoCalResult:
    cam_id: str
    ok: bool
    num_markers: int = 0
    residual_px_mean: float = 0.0
    residual_px_max: float = 0.0
    error: Optional[str] = None
    fit: Optional[CameraFit] = None


@dataclass
class LedAutoCalConfig:
    marker_step: int = 5
    settle_ms: int = 400
    min_markers: int = 6
    save_residual_px_max: float = 8.0
    strip_names: Optional[list[str]] = None
    dry_run: bool = False


def detect_bright_centroid(
    frame_bgr: np.ndarray,
    *,
    min_brightness: int = 180,
    percentile: float = 97.0,
) -> Optional[tuple[float, float]]:
    """Return (u, v) centroid of the brightest blob, or None."""

    if frame_bgr is None or frame_bgr.size == 0:
        return None
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh_val = max(min_brightness, float(np.percentile(gray, percentile)))
    _, mask = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    best = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(best)
    if area < 12.0:
        return None
    m = cv2.moments(best)
    if abs(m["m00"]) < 1e-6:
        return None
    u = float(m["m10"] / m["m00"])
    v = float(m["m01"] / m["m00"])
    return u, v


def _copy_maro_light_defaults(cache_dir: Path) -> None:
    """Seed cache from maro-light-tools meter_test config when cache is empty."""

    repo_root = Path(__file__).resolve().parents[2]
    src_strips = repo_root / "maro-light-tools" / "meter_test" / "config" / "strips.json"
    src_map = repo_root / "maro-light-tools" / "meter_test" / "config" / "artnet_mapping.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst_strips = cache_dir / "strips.json"
    dst_map = cache_dir / "artnet_mapping.json"
    def _cache_strips_empty() -> bool:
        if not dst_strips.is_file():
            return True
        try:
            doc = json.loads(dst_strips.read_text(encoding="utf-8"))
            strips = doc.get("strips") if isinstance(doc, dict) else doc
            return not strips
        except (json.JSONDecodeError, OSError):
            return True

    if src_strips.is_file() and (not dst_strips.is_file() or _cache_strips_empty()):
        data = json.loads(src_strips.read_text(encoding="utf-8"))
        dst_strips.write_text(
            json.dumps({"strips": data if isinstance(data, list) else data}, indent=2),
            encoding="utf-8",
        )
    if src_map.is_file() and not dst_map.is_file():
        dst_map.write_text(src_map.read_text(encoding="utf-8"), encoding="utf-8")


class LedAutoCalibrator:
    def __init__(
        self,
        *,
        led: LedMarker,
        floorplan: MaroFloorplanAssets,
        cfg: LedAutoCalConfig,
        grab_frame: Callable[[str], Optional[np.ndarray]],
    ):
        self._led = led
        self._floorplan = floorplan
        self._cfg = cfg
        self._grab = grab_frame

    @classmethod
    def from_paths(
        cls,
        *,
        cache_dir: Path,
        maro_api_base: str,
        cfg: LedAutoCalConfig,
        grab_frame: Callable[[str], Optional[np.ndarray]],
        strips_path: Optional[Path] = None,
        mapping_path: Optional[Path] = None,
    ) -> LedAutoCalibrator:
        cache_dir = Path(cache_dir)
        _copy_maro_light_defaults(cache_dir)
        if strips_path and mapping_path:
            cache_dir.mkdir(parents=True, exist_ok=True)
            strips_data = json.loads(Path(strips_path).read_text(encoding="utf-8"))
            strips = strips_data.get("strips") if isinstance(strips_data, dict) else strips_data
            mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
            (cache_dir / "strips.json").write_text(
                json.dumps({"strips": strips}, indent=2), encoding="utf-8"
            )
            (cache_dir / "artnet_mapping.json").write_text(
                json.dumps(mapping, indent=2), encoding="utf-8"
            )
        floorplan = load_maro_floorplan(maro_api_base, cache_dir)
        led = LedMarker.from_cache(cache_dir)
        return cls(led=led, floorplan=floorplan, cfg=cfg, grab_frame=grab_frame)

    def calibrate_camera(
        self,
        cam_id: str,
        *,
        strip_name: Optional[str] = None,
    ) -> LedAutoCalResult:
        strips = self._cfg.strip_names or self._led.strip_names()
        if strip_name:
            strips = [strip_name] if strip_name in self._led.strip_names() else []
        if not strips:
            return LedAutoCalResult(
                cam_id=cam_id,
                ok=False,
                error="no LED strips configured (check strips.json + artnet_mapping.json)",
            )

        image_points: list[tuple[float, float]] = []
        world_points: list[tuple[float, float]] = []
        position_ids: list[str] = []
        step = max(1, int(self._cfg.marker_step))

        try:
            for sname in strips:
                info = self._led.info(sname)
                if not info:
                    continue
                n_markers = int(info["num_markers"])
                for k in range(0, n_markers, step):
                    pid = f"{sname}:m{k}"
                    if self._cfg.dry_run:
                        continue
                    self._led.all_off()
                    lit = self._led.light_marker(sname, k)
                    if not lit.get("ok"):
                        continue
                    time.sleep(self._cfg.settle_ms / 1000.0)
                    frame = self._grab(cam_id)
                    if frame is None:
                        continue
                    centroid = detect_bright_centroid(frame)
                    if centroid is None:
                        continue
                    mm = self._led.marker_position_mm(sname, k)
                    if mm is None:
                        continue
                    px_x, px_y = self._floorplan.mm_to_plan_px(mm[0], mm[1])
                    image_points.append(centroid)
                    world_points.append((px_x, px_y))
                    position_ids.append(pid)
        finally:
            if not self._cfg.dry_run:
                self._led.all_off()

        if self._cfg.dry_run:
            return LedAutoCalResult(
                cam_id=cam_id,
                ok=False,
                error="dry_run: no correspondences collected",
            )

        n = len(image_points)
        if n < self._cfg.min_markers:
            return LedAutoCalResult(
                cam_id=cam_id,
                ok=False,
                num_markers=n,
                error=f"need >={self._cfg.min_markers} markers, got {n}",
            )

        src = np.array(image_points, dtype=np.float64)
        dst = np.array(world_points, dtype=np.float64)
        fit = _fit_one(cam_id, src, dst, position_ids)
        if fit.error or fit.H is None:
            return LedAutoCalResult(
                cam_id=cam_id,
                ok=False,
                num_markers=n,
                error=fit.error or "homography fit failed",
                fit=fit,
            )
        if fit.residual_px_mean > self._cfg.save_residual_px_max:
            return LedAutoCalResult(
                cam_id=cam_id,
                ok=False,
                num_markers=n,
                residual_px_mean=fit.residual_px_mean,
                residual_px_max=fit.residual_px_max,
                error=(
                    f"mean residual {fit.residual_px_mean:.1f}px > "
                    f"{self._cfg.save_residual_px_max}px"
                ),
                fit=fit,
            )
        return LedAutoCalResult(
            cam_id=cam_id,
            ok=True,
            num_markers=n,
            residual_px_mean=fit.residual_px_mean,
            residual_px_max=fit.residual_px_max,
            fit=fit,
        )

    def build_calibration_entry(
        self,
        result: LedAutoCalResult,
        *,
        img_w: int,
        img_h: int,
    ) -> dict[str, Any]:
        if result.fit is None or result.fit.H is None:
            raise ValueError("no fit on result")
        fit = result.fit
        calibrated_at = datetime.now(timezone.utc).isoformat()
        return {
            "mode": "homography",
            "coordinate_space": "maro_floorplan_px",
            "plan_bounds_px": {
                "x_min": 0.0,
                "y_min": 0.0,
                "x_max": float(self._floorplan.plan_width),
                "y_max": float(self._floorplan.plan_height),
            },
            "H": [[float(v) for v in row] for row in fit.H.tolist()],
            "calib_image_width": int(img_w),
            "calib_image_height": int(img_h),
            "calibrated_at": calibrated_at,
            "image_points": [
                [int(round(u)), int(round(v))] for u, v in fit.image_points
            ],
            "world_points_plan_px": [
                [float(x), float(y)] for x, y in fit.world_points
            ],
            "_comment": (
                f"LED auto-cal. residual mean={fit.residual_px_mean:.1f}px "
                f"max={fit.residual_px_max:.1f}px n={fit.num_points}."
            ),
        }
