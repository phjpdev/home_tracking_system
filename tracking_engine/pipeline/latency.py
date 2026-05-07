"""Rolling per-stage latency stats (milliseconds)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict


@dataclass
class StageStats:
    samples: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    last_ms: float = 0.0

    def record(self, ms: float) -> None:
        self.last_ms = ms
        self.samples.append(ms)

    def mean_ms(self) -> float:
        if not self.samples:
            return 0.0
        return float(sum(self.samples) / len(self.samples))


@dataclass
class LatencyMonitor:
    stages: Dict[str, StageStats] = field(default_factory=dict)

    def touch(self, name: str) -> None:
        if name not in self.stages:
            self.stages[name] = StageStats()

    def record(self, name: str, ms: float) -> None:
        self.touch(name)
        self.stages[name].record(ms)

    def summary_line(self) -> str:
        parts = []
        for k in ("grab", "detect", "track", "geom", "post", "frame_total"):
            if k in self.stages:
                st = self.stages[k]
                parts.append(f"{k}={st.last_ms:.2f}ms")
        return "  ".join(parts)
