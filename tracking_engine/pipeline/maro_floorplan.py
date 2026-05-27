"""Load Maro floor-plan assets (API + disk cache) and mm ↔ plan-pixel transforms."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

COORDINATE_SPACE = "maro_floorplan_px"
COORD_MARO_PLAN_PX = COORDINATE_SPACE
DEFAULT_PLAN_SIZE = (2700, 1324)


@dataclass
class PlanBoundsMm:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @classmethod
    def from_floorplan_json(cls, data: dict[str, Any]) -> PlanBoundsMm:
        b = data.get("bounds") or {}
        if b:
            return cls(
                x_min=float(b.get("min_x", b.get("x_min", 0))),
                x_max=float(b.get("max_x", b.get("x_max", 1))),
                y_min=float(b.get("min_y", b.get("y_min", 0))),
                y_max=float(b.get("max_y", b.get("y_max", 1))),
            )
        walls = data.get("walls") or []
        xs: list[float] = []
        ys: list[float] = []
        for edge in walls:
            for key in ("a", "b", "start", "end"):
                pt = edge.get(key)
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
            if "x1" in edge and "y1" in edge:
                xs.extend([float(edge["x1"]), float(edge.get("x2", edge["x1"]))])
                ys.extend([float(edge["y1"]), float(edge.get("y2", edge["y1"]))])
        if not xs:
            return cls(-6193.0, 11072.0, -5624.0, 2843.0)
        pad = 50.0
        return cls(min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad)


@dataclass
class MaroFloorplanAssets:
    api_base: str
    plan_width: int
    plan_height: int
    bounds_mm: PlanBoundsMm
    bg_png: bytes
    zones: list[dict[str, Any]] = field(default_factory=list)
    spots: list[dict[str, Any]] = field(default_factory=list)
    strips: list[dict[str, Any]] = field(default_factory=list)
    source: str = "api"

    def mm_to_plan_px(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        b = self.bounds_mm
        w, h = self.plan_width, self.plan_height
        span_x = max(b.x_max - b.x_min, 1e-6)
        span_y = max(b.y_max - b.y_min, 1e-6)
        px_x = (x_mm - b.x_min) / span_x * w
        nu = (y_mm - b.y_min) / span_y
        px_y = (1.0 - nu) * h
        return px_x, px_y

    def overlay_for_ui(self) -> dict[str, Any]:
        zones_out: list[dict[str, Any]] = []
        for z in self.zones:
            poly_mm = z.get("polygon") or z.get("points") or z.get("polygon_mm") or []
            poly_px = [
                list(self.mm_to_plan_px(float(p[0]), float(p[1])))
                for p in poly_mm
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            zones_out.append(
                {
                    "name": z.get("name") or z.get("id") or "",
                    "color": z.get("color"),
                    "polygon_px": poly_px,
                }
            )
        spots_out: list[dict[str, Any]] = []
        for s in self.spots:
            pos = s.get("position") or {}
            x_mm = float(pos.get("x", s.get("x", 0)))
            y_mm = float(pos.get("y", s.get("y", 0)))
            px_x, px_y = self.mm_to_plan_px(x_mm, y_mm)
            size_mm = float(s.get("size", 200))
            span_x = max(self.bounds_mm.x_max - self.bounds_mm.x_min, 1e-6)
            r_px = max(3.0, (size_mm / span_x) * self.plan_width * 0.5)
            spots_out.append(
                {"name": s.get("name", ""), "x_px": px_x, "y_px": px_y, "r_px": r_px}
            )
        strips_out: list[dict[str, Any]] = []
        for st in self.strips:
            pts_mm = st.get("points") or []
            pts_px = [
                list(self.mm_to_plan_px(float(p[0]), float(p[1])))
                for p in pts_mm
                if isinstance(p, (list, tuple)) and len(p) >= 2
            ]
            strips_out.append({"name": st.get("name", ""), "points_px": pts_px})
        return {"zones": zones_out, "spots": spots_out, "strips": strips_out}

    def meta_dict(self) -> dict[str, Any]:
        b = self.bounds_mm
        return {
            "coordinate_space": COORDINATE_SPACE,
            "plan_width": self.plan_width,
            "plan_height": self.plan_height,
            "bounds_mm": {
                "x_min": b.x_min,
                "x_max": b.x_max,
                "y_min": b.y_min,
                "y_max": b.y_max,
            },
            "api_base": self.api_base,
            "source": self.source,
        }


def _cache_paths(cache_dir: Path) -> dict[str, Path]:
    return {
        "floorplan": cache_dir / "floorplan.json",
        "bg": cache_dir / "floorplan_bg.png",
        "zones": cache_dir / "zones.json",
        "spots": cache_dir / "spots.json",
        "strips": cache_dir / "strips.json",
    }


def _read_cache(paths: dict[str, Path]) -> Optional[MaroFloorplanAssets]:
    if not paths["floorplan"].is_file() or not paths["bg"].is_file():
        return None
    floorplan = json.loads(paths["floorplan"].read_text(encoding="utf-8"))
    bg = paths["bg"].read_bytes()
    zones_raw = json.loads(paths["zones"].read_text(encoding="utf-8")) if paths["zones"].is_file() else []
    spots_raw = json.loads(paths["spots"].read_text(encoding="utf-8")) if paths["spots"].is_file() else {}
    strips_raw = json.loads(paths["strips"].read_text(encoding="utf-8")) if paths["strips"].is_file() else {}
    zones = zones_raw if isinstance(zones_raw, list) else zones_raw.get("zones", [])
    spots = spots_raw.get("spots", spots_raw) if isinstance(spots_raw, dict) else spots_raw
    strips = strips_raw.get("strips", strips_raw) if isinstance(strips_raw, dict) else strips_raw
    if not isinstance(spots, list):
        spots = []
    if not isinstance(strips, list):
        strips = []
    bounds = PlanBoundsMm.from_floorplan_json(floorplan)
    w = int(floorplan.get("width") or DEFAULT_PLAN_SIZE[0])
    h = int(floorplan.get("height") or DEFAULT_PLAN_SIZE[1])
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(bg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            h, w = int(img.shape[0]), int(img.shape[1])
    except Exception:
        pass
    return MaroFloorplanAssets(
        api_base="cache",
        plan_width=w,
        plan_height=h,
        bounds_mm=bounds,
        bg_png=bg,
        zones=list(zones),
        spots=list(spots),
        strips=list(strips),
        source="cache",
    )


def load_maro_floorplan(
    api_base: str,
    cache_dir: Path,
    timeout_sec: float = 8.0,
) -> MaroFloorplanAssets:
    api_base = api_base.rstrip("/")
    paths = _cache_paths(cache_dir)
    try:
        floorplan = requests.get(f"{api_base}/api/floorplan", timeout=timeout_sec).json()
        bg = requests.get(f"{api_base}/api/floorplan/bg", timeout=timeout_sec).content
        zones = requests.get(f"{api_base}/api/zones", timeout=timeout_sec).json()
        spots = requests.get(f"{api_base}/api/spots", timeout=timeout_sec).json()
        strips = requests.get(f"{api_base}/api/strips", timeout=timeout_sec).json()
        paths["floorplan"].parent.mkdir(parents=True, exist_ok=True)
        paths["floorplan"].write_text(json.dumps(floorplan), encoding="utf-8")
        paths["bg"].write_bytes(bg)
        paths["zones"].write_text(json.dumps(zones), encoding="utf-8")
        paths["spots"].write_text(json.dumps(spots), encoding="utf-8")
        paths["strips"].write_text(json.dumps(strips), encoding="utf-8")
        bounds = PlanBoundsMm.from_floorplan_json(floorplan)
        w, h = DEFAULT_PLAN_SIZE
        try:
            import cv2
            import numpy as np

            img = cv2.imdecode(np.frombuffer(bg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                h, w = int(img.shape[0]), int(img.shape[1])
        except Exception:
            pass
        zones_list = zones if isinstance(zones, list) else zones.get("zones", [])
        spots_list = spots.get("spots", spots) if isinstance(spots, dict) else spots
        strips_list = strips.get("strips", strips) if isinstance(strips, dict) else strips
        logger.info("maro_floorplan: loaded from API %s (%dx%d)", api_base, w, h)
        return MaroFloorplanAssets(
            api_base=api_base,
            plan_width=w,
            plan_height=h,
            bounds_mm=bounds,
            bg_png=bg,
            zones=list(zones_list) if isinstance(zones_list, list) else [],
            spots=list(spots_list) if isinstance(spots_list, list) else [],
            strips=list(strips_list) if isinstance(strips_list, list) else [],
            source="api",
        )
    except Exception as exc:
        logger.warning("maro_floorplan: API failed (%s), using cache", exc)
        cached = _read_cache(paths)
        if cached is not None:
            return cached
        raise RuntimeError(
            f"could not load Maro floor plan from {api_base} and no cache at {cache_dir}"
        ) from exc
