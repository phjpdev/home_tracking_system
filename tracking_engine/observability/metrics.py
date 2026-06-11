"""Prometheus metrics module.

The module exposes a singleton ``metrics`` object whose attributes are
``Counter``, ``Histogram``, and ``Gauge`` instances. If
``prometheus_client`` is not installed the module replaces every metric
with a no-op proxy so the rest of the code stays free of import guards.
"""

from __future__ import annotations

import sys
from typing import Any, Optional

try:
    from prometheus_client import (  # type: ignore
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        start_http_server,
    )
    _HAVE_PROM = True
except ImportError:
    _HAVE_PROM = False


class _NoopMetric:
    def labels(self, *_args, **_kwargs) -> "_NoopMetric":
        return self

    def inc(self, *_a, **_k) -> None:
        return None

    def dec(self, *_a, **_k) -> None:
        return None

    def set(self, *_a, **_k) -> None:
        return None

    def observe(self, *_a, **_k) -> None:
        return None


class _Metrics:
    def __init__(self) -> None:
        if _HAVE_PROM:
            reg = CollectorRegistry()
            self.registry = reg
            self.body_match_score = Histogram(
                "reid_body_match_score",
                "Cosine distance for nearest body neighbour per observe() call",
                buckets=(0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0),
                registry=reg,
            )
            self.face_match_score = Histogram(
                "reid_face_match_score",
                "Cosine distance for nearest face neighbour per fused tick",
                buckets=(0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0),
                registry=reg,
            )
            self.gallery_size = Gauge(
                "reid_gallery_size",
                "Number of appearance embeddings per identity (NULL identity -> 'anonymous')",
                ["identity_id"],
                registry=reg,
            )
            self.inference_latency_ms = Histogram(
                "reid_inference_latency_ms",
                "Latency in milliseconds per pipeline stage",
                ["stage"],
                buckets=(1, 2, 5, 10, 20, 50, 100, 200, 500, 1000),
                registry=reg,
            )
            self.track_state = Gauge(
                "reid_track_state",
                "Live count of tracks per state",
                ["state"],
                registry=reg,
            )
            self.thermal_fall_events = Counter(
                "thermal_fall_events_total",
                "Fall events emitted by the thermal pipeline",
                ["room", "confidence"],
                registry=reg,
            )
            self.post_failures = Counter(
                "tracking_post_failures_total",
                "HTTP POST failures to Maro",
                ["endpoint"],
                registry=reg,
            )
            self.thermal_last_event_age = Gauge(
                "thermal_last_event_age_sec",
                "Seconds since the last successful fall-detection heartbeat/event per room",
                ["room"],
                registry=reg,
            )
            self.thermal_position_posts = Counter(
                "thermal_position_posts_total",
                "Position POSTs emitted by the thermal pipeline",
                ["room"],
                registry=reg,
            )
            self.thermal_blob_area = Gauge(
                "thermal_blob_area_pixels",
                "Area of the current heat blob in sensor pixels",
                ["room"],
                registry=reg,
            )
            self.thermal_room_online = Gauge(
                "thermal_room_online",
                "1 when the ESP32 node heartbeat reports online",
                ["room"],
                registry=reg,
            )
            self.thermal_centroid_plan_px = Gauge(
                "thermal_centroid_plan_px",
                "Smoothed thermal centroid on the Maro floor plan (pixels)",
                ["room", "axis"],
                registry=reg,
            )
        else:
            print(
                "[obs] prometheus_client not installed; metrics are no-ops",
                file=sys.stderr,
            )
            self.registry = None
            self.body_match_score = _NoopMetric()
            self.face_match_score = _NoopMetric()
            self.gallery_size = _NoopMetric()
            self.inference_latency_ms = _NoopMetric()
            self.track_state = _NoopMetric()
            self.thermal_fall_events = _NoopMetric()
            self.post_failures = _NoopMetric()
            self.thermal_last_event_age = _NoopMetric()
            self.thermal_position_posts = _NoopMetric()
            self.thermal_blob_area = _NoopMetric()
            self.thermal_room_online = _NoopMetric()
            self.thermal_centroid_plan_px = _NoopMetric()


metrics = _Metrics()


def start_metrics_server(port: int = 9100, addr: str = "0.0.0.0") -> Optional[Any]:
    """Spawn an HTTP server exposing ``/metrics`` on ``port``."""
    if not _HAVE_PROM or metrics.registry is None:
        return None
    server, thread = start_http_server(port=int(port), addr=str(addr), registry=metrics.registry)
    print(f"[obs] /metrics listening on http://{addr}:{port}", file=sys.stderr)
    return server
