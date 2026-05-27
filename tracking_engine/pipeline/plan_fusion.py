"""Fuse multi-camera plan positions, zone clip, temporal EMA smoothing."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    m = len(s) // 2
    if len(s) % 2:
        return s[m]
    return 0.5 * (s[m - 1] + s[m])


def _hypot(dx: float, dy: float) -> float:
    return (dx * dx + dy * dy) ** 0.5


def _point_in_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    if len(poly) < 3:
        return False
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


@dataclass
class ZonePlanPx:
    name: str
    polygon_px: list[tuple[float, float]]


@dataclass
class PlanFusionConfig:
    enabled: bool = True
    cluster_distance_px: float = 80.0
    ema_alpha: float = 0.35
    ema_reset_gap_sec: float = 2.0
    drop_outside_zones: bool = True


class PlanFusionCoordinator:
    def __init__(self, cfg: PlanFusionConfig, zones: list[ZonePlanPx]):
        self._cfg = cfg
        self._zones = zones
        self._ema: dict[str, tuple[float, float]] = {}
        self._ema_ts: dict[str, float] = {}

    def _zone_for_point(self, x: float, y: float) -> str:
        for z in self._zones:
            if _point_in_polygon(x, y, z.polygon_px):
                return z.name
        return "unknown"

    def fuse_tick(self, rows: list[dict[str, Any]], ts: float) -> list[dict[str, Any]]:
        if not self._cfg.enabled or not rows:
            return rows

        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            gid = row.get("global_id")
            key = f"gid:{gid}" if gid else f"{row.get('cam_id')}:{row.get('id')}"
            buckets.setdefault(key, []).append(row)

        keys = list(buckets.keys())
        parent: dict[str, str] = {k: k for k in keys}

        def find(k: str) -> str:
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for i, ki in enumerate(keys):
            ci = buckets[ki]
            xi = _median([float(r["x"]) for r in ci])
            yi = _median([float(r["y"]) for r in ci])
            for j in range(i + 1, len(keys)):
                kj = keys[j]
                cj = buckets[kj]
                xj = _median([float(r["x"]) for r in cj])
                yj = _median([float(r["y"]) for r in cj])
                if _hypot(xi - xj, yi - yj) <= self._cfg.cluster_distance_px:
                    union(ki, kj)

        groups: dict[str, list[dict[str, Any]]] = {}
        for k in keys:
            groups.setdefault(find(k), []).extend(buckets[k])

        fused: list[dict[str, Any]] = []
        for key, group in groups.items():
            xs = [float(r["x"]) for r in group]
            ys = [float(r["y"]) for r in group]
            x_med = _median(xs)
            y_med = _median(ys)
            sources = sorted({str(r["cam_id"]) for r in group})
            gid = next((r.get("global_id") for r in group if r.get("global_id")), None)
            track_id = str(gid) if gid else key
            x_sm, y_sm = self._smooth(track_id, x_med, y_med, ts)
            zone = self._zone_for_point(x_sm, y_sm)
            if self._cfg.drop_outside_zones and zone == "unknown" and self._zones:
                continue
            person: dict[str, Any] = {
                "id": str(group[0].get("id", "t0")),
                "x": int(round(x_sm)),
                "y": int(round(y_sm)),
                "zone": zone,
                "privacy": bool(group[0].get("privacy", False)),
                "sources": sources,
            }
            if gid:
                person["global_id"] = gid
            fused.append(person)
        return fused

    def _smooth(self, track_id: str, x: float, y: float, ts: float) -> tuple[float, float]:
        prev = self._ema.get(track_id)
        prev_ts = self._ema_ts.get(track_id, 0.0)
        if prev is None or (ts - prev_ts) > self._cfg.ema_reset_gap_sec:
            self._ema[track_id] = (x, y)
            self._ema_ts[track_id] = ts
            return x, y
        a = self._cfg.ema_alpha
        nx = a * x + (1.0 - a) * prev[0]
        ny = a * y + (1.0 - a) * prev[1]
        self._ema[track_id] = (nx, ny)
        self._ema_ts[track_id] = ts
        return nx, ny


def zones_from_overlay(overlay: dict[str, Any]) -> list[ZonePlanPx]:
    out: list[ZonePlanPx] = []
    for z in overlay.get("zones", []):
        poly = [(float(p[0]), float(p[1])) for p in z.get("polygon_px", [])]
        if poly:
            out.append(ZonePlanPx(name=str(z.get("name", "")), polygon_px=poly))
    return out
