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
    prefer_stereo: bool = True
    # Velocity outlier gate — reject single-frame jumps above this speed.
    # plan is ~6.4 mm/px, so 800 px/s ≈ 5 m/s (running). 1500 px/s ≈ 9.6 m/s (sprint).
    max_speed_pxps: float = 1500.0
    # After this many consecutive outliers, accept the new value (real teleport / re-id swap).
    outlier_release_after: int = 5
    # Temporal hold: keep re-emitting a fused dot at its last position for this many
    # seconds after it was last seen, so detections that flicker out for a tick or two
    # (single-camera objects, brief occlusion) don't blink off. 0 disables (default).
    hold_sec: float = 0.0


class PlanFusionCoordinator:
    def __init__(self, cfg: PlanFusionConfig, zones: list[ZonePlanPx]):
        self._cfg = cfg
        self._zones = zones
        self._ema: dict[str, tuple[float, float]] = {}
        self._ema_ts: dict[str, float] = {}
        self._outlier_count: dict[str, int] = {}
        # Temporal hold store: stable_key -> {"person": dict, "ts": float}
        self._held: dict[str, dict[str, Any]] = {}
        self._hold_counter: int = 0

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
            stereo_rows = [
                r for r in group
                if str(r.get("position_source")) == "stereo"
            ]
            if self._cfg.prefer_stereo and stereo_rows:
                use_group = stereo_rows
            else:
                use_group = group
            xs = [float(r["x"]) for r in use_group]
            ys = [float(r["y"]) for r in use_group]
            x_med = _median(xs)
            y_med = _median(ys)
            sources = sorted({str(r["cam_id"]) for r in group})
            gid = next((r.get("global_id") for r in group if r.get("global_id")), None)
            track_id = str(gid) if gid else key
            x_sm, y_sm = self._smooth(track_id, x_med, y_med, ts)
            zone = self._zone_for_point(x_sm, y_sm)
            if zone == "unknown":
                for r in group:
                    z = r.get("zone")
                    if z and str(z) != "unknown":
                        zone = str(z)
                        break
            if self._cfg.drop_outside_zones and zone == "unknown" and self._zones:
                continue
            pos_src = "stereo" if stereo_rows and self._cfg.prefer_stereo else "fused"
            cal_conf = "high" if stereo_rows else "low"
            person: dict[str, Any] = {
                "id": str(group[0].get("id", "t0")),
                "x": int(round(x_sm)),
                "y": int(round(y_sm)),
                "zone": zone,
                "privacy": bool(group[0].get("privacy", False)),
                "sources": sources,
                "position_source": pos_src,
                "cal_confidence": cal_conf,
            }
            if gid:
                person["global_id"] = gid
            fused.append(person)

        return self._apply_hold(fused, ts)

    def _apply_hold(
        self, fused: list[dict[str, Any]], ts: float
    ) -> list[dict[str, Any]]:
        """Re-emit recently-seen dots that vanished for a few ticks.

        Matches this tick's fused dots to previously held ones by proximity
        (``cluster_distance_px``), refreshes the matches, and re-emits any held
        dot that wasn't seen this tick but is still within ``hold_sec``.
        """
        if self._cfg.hold_sec <= 0:
            return fused

        radius = self._cfg.cluster_distance_px
        used_keys: set[str] = set()

        # Refresh held entries with this tick's detections (proximity match).
        for person in fused:
            px, py = float(person["x"]), float(person["y"])
            best_key: Optional[str] = None
            best_dist = radius
            for hk, h in self._held.items():
                if hk in used_keys:
                    continue
                hp = h["person"]
                d = _hypot(px - float(hp["x"]), py - float(hp["y"]))
                if d <= best_dist:
                    best_dist = d
                    best_key = hk
            if best_key is None:
                self._hold_counter += 1
                best_key = f"h{self._hold_counter}"
            used_keys.add(best_key)
            self._held[best_key] = {"person": person, "ts": ts}

        # Re-emit held dots that weren't seen this tick but are still fresh.
        out = list(fused)
        for hk, h in self._held.items():
            if hk in used_keys:
                continue
            if ts - float(h["ts"]) <= self._cfg.hold_sec:
                held_person = dict(h["person"])
                held_person["held"] = True
                out.append(held_person)

        # Prune expired entries.
        self._held = {
            hk: h
            for hk, h in self._held.items()
            if ts - float(h["ts"]) <= self._cfg.hold_sec
        }
        return out

    def _smooth(self, track_id: str, x: float, y: float, ts: float) -> tuple[float, float]:
        prev = self._ema.get(track_id)
        prev_ts = self._ema_ts.get(track_id, 0.0)
        if prev is None or (ts - prev_ts) > self._cfg.ema_reset_gap_sec:
            self._ema[track_id] = (x, y)
            self._ema_ts[track_id] = ts
            self._outlier_count[track_id] = 0
            return x, y

        dt = max(ts - prev_ts, 1e-3)
        dist = _hypot(x - prev[0], y - prev[1])
        speed = dist / dt

        if self._cfg.max_speed_pxps > 0 and speed > self._cfg.max_speed_pxps:
            # Outlier — reject this measurement, keep the previous smoothed value.
            cnt = self._outlier_count.get(track_id, 0) + 1
            self._outlier_count[track_id] = cnt
            if cnt < self._cfg.outlier_release_after:
                self._ema_ts[track_id] = ts  # advance time so we don't trigger reset_gap
                return prev[0], prev[1]
            # too many outliers in a row → trust the new measurement (teleport / track swap)

        self._outlier_count[track_id] = 0
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
