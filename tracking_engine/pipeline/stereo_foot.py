"""Stereo foot fusion for overlapping camera pairs on the Maro floor plan."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional


def _hypot(dx: float, dy: float) -> float:
    return math.hypot(dx, dy)


@dataclass
class StereoFootConfig:
    pairs: list[tuple[str, str]] = field(default_factory=list)
    max_match_plan_px: float = 150.0
    max_reproj_error_px: float = 12.0


def fuse_stereo_plan_points(
    plan_a: tuple[float, float],
    plan_b: tuple[float, float],
    *,
    max_plan_distance_px: float,
) -> Optional[tuple[float, float, float]]:
    """Fuse two homography-based plan points when they agree. Returns (x, y, reproj_px)."""

    dist = _hypot(plan_a[0] - plan_b[0], plan_a[1] - plan_b[1])
    if dist > max_plan_distance_px:
        return None
    x = 0.5 * (plan_a[0] + plan_b[0])
    y = 0.5 * (plan_a[1] + plan_b[1])
    return x, y, dist * 0.5


class StereoFootCoordinator:
    def __init__(self, cfg: StereoFootConfig):
        self._cfg = cfg
        self._pairs = [(a, b) for a, b in cfg.pairs]

    def merge_fusion_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows or not self._pairs:
            return rows

        by_cam: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_cam.setdefault(str(r.get("cam_id")), []).append(r)

        consumed: set[int] = set()
        out: list[dict[str, Any]] = []

        for cam_a, cam_b in self._pairs:
            list_a = by_cam.get(cam_a) or []
            list_b = by_cam.get(cam_b) or []
            used_b: set[int] = set()
            for ra in list_a:
                id_a = id(ra)
                best_j = -1
                best_dist = self._cfg.max_match_plan_px + 1.0
                for j, rb in enumerate(list_b):
                    if j in used_b:
                        continue
                    if ra.get("global_id") and rb.get("global_id"):
                        if ra.get("global_id") != rb.get("global_id"):
                            continue
                    dist = _hypot(
                        float(ra["x"]) - float(rb["x"]),
                        float(ra["y"]) - float(rb["y"]),
                    )
                    if dist < best_dist:
                        best_dist = dist
                        best_j = j
                if best_j < 0:
                    continue
                rb = list_b[best_j]
                fused = fuse_stereo_plan_points(
                    (float(ra["x"]), float(ra["y"])),
                    (float(rb["x"]), float(rb["y"])),
                    max_plan_distance_px=self._cfg.max_match_plan_px,
                )
                if fused is None:
                    continue
                x, y, reproj = fused
                if reproj > self._cfg.max_reproj_error_px:
                    continue
                used_b.add(best_j)
                consumed.add(id_a)
                consumed.add(id(rb))
                gid = ra.get("global_id") or rb.get("global_id")
                zone = ra.get("zone") or rb.get("zone") or "unknown"
                out.append(
                    {
                        "cam_id": f"{cam_a}+{cam_b}",
                        "id": str(ra.get("id", "t0")),
                        "x": int(round(x)),
                        "y": int(round(y)),
                        "privacy": bool(ra.get("privacy", False)),
                        "global_id": gid,
                        "position_source": "stereo",
                        "cal_confidence": "high",
                        "zone": zone,
                        "stereo_reproj_px": round(reproj, 2),
                    }
                )

        for r in rows:
            if id(r) not in consumed:
                out.append(r)
        return out
