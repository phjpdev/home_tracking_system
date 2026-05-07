"""Per-camera ByteTrack (supervision)."""

from __future__ import annotations


class ByteTracker:
    def __init__(self, **kwargs):
        import supervision as sv

        self._tracker = sv.ByteTrack(**kwargs) if kwargs else sv.ByteTrack()

    def update(self, detections):
        return self._tracker.update_with_detections(detections)
