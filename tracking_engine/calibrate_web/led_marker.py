"""LED marker mode — light a single pixel-group on an ARGB strip via Art-Net.

Maro-independent: sends Art-Net UDP packets directly to the Pixelator MK2.

A marker = one logical pixel (which is `pixel_grouping` physical LEDs at 60 LED/m
on WS2814 strips, so 6 LEDs = 100 mm = 10 cm spacing). The plan-pixel position
of marker N on a strip is found by walking the strip path from `points[]` +
`segment_lengths` and placing the marker at the center of its 6-LED group.
"""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ARTNET_PORT = 6454
CHANNELS_PER_UNIVERSE = 512
CHANNELS_PER_PIXEL = 4  # RGBW
LEDS_PER_METER = 60
LED_PITCH_MM = 1000.0 / LEDS_PER_METER  # 16.667 mm per physical LED
PIXELS_PER_UNIVERSE = CHANNELS_PER_UNIVERSE // CHANNELS_PER_PIXEL  # 128


def _make_artdmx(universe: int, data: bytes) -> bytes:
    header = b"Art-Net\x00"
    opcode = struct.pack("<H", 0x5000)
    proto = struct.pack(">H", 14)
    seq = b"\x00"
    physical = b"\x00"
    univ = struct.pack("<H", universe)
    length = struct.pack(">H", len(data))
    return header + opcode + proto + seq + physical + univ + length + data


@dataclass
class StripGeom:
    name: str
    points_mm: list[list[float]]    # path waypoints (mm)
    segment_lengths_mm: list[float] # length per segment
    total_mm: float
    # Mapping (Pixelator side)
    controller_ip: str
    start_universe: int
    start_channel: int      # 1-based
    pixel_grouping: int
    num_universes: int
    reverse: bool

    @property
    def marker_spacing_mm(self) -> float:
        """One marker = one logical pixel = pixel_grouping LEDs."""
        return LED_PITCH_MM * self.pixel_grouping

    def num_markers(self) -> int:
        """How many full markers fit on this strip."""
        return int(self.total_mm // self.marker_spacing_mm)

    def marker_position_mm(self, marker_idx: int) -> tuple[float, float]:
        """Center-of-group mm coordinate for the Nth marker, walking the path."""
        target = (marker_idx + 0.5) * self.marker_spacing_mm
        acc = 0.0
        for i, seg_len in enumerate(self.segment_lengths_mm):
            if acc + seg_len >= target:
                t = (target - acc) / seg_len if seg_len else 0.0
                p0 = self.points_mm[i]
                p1 = self.points_mm[i + 1]
                return (p0[0] + (p1[0] - p0[0]) * t,
                        p0[1] + (p1[1] - p0[1]) * t)
            acc += seg_len
        return tuple(self.points_mm[-1])


class LedMarker:
    """Holds strips + mapping, sends Art-Net to light one marker at a time."""

    def __init__(self, strips_data: list[dict[str, Any]], mapping: dict[str, Any]):
        self._strips: dict[str, StripGeom] = {}
        controllers_by_id: dict[str, dict[str, Any]] = {
            c["id"]: c for c in mapping.get("controllers", [])
        }
        for s in strips_data:
            if str(s.get("type", "")).lower() != "argb":
                continue
            name = s["name"]
            m = mapping.get("strip_mappings", {}).get(name)
            if not m:
                continue
            controller = controllers_by_id.get(m.get("controller_id"))
            if not controller:
                continue
            self._strips[name] = StripGeom(
                name=name,
                points_mm=list(s["points"]),
                segment_lengths_mm=list(s["segment_lengths"]),
                total_mm=float(sum(s["segment_lengths"])),
                controller_ip=str(controller.get("ip", "")),
                start_universe=int(m.get("start_universe", 0)),
                start_channel=int(m.get("start_channel", 1)),
                pixel_grouping=int(m.get("pixel_grouping", 6)),
                num_universes=int(m.get("num_universes", 1)),
                reverse=bool(m.get("reverse", False)),
            )
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    @classmethod
    def from_cache(cls, cache_dir: Path) -> "LedMarker":
        cache_dir = Path(cache_dir)
        strips_doc = json.loads((cache_dir / "strips.json").read_text())
        strips = strips_doc.get("strips") if isinstance(strips_doc, dict) else strips_doc
        mapping_path = cache_dir / "artnet_mapping.json"
        if mapping_path.exists():
            mapping = json.loads(mapping_path.read_text())
        else:
            mapping = {"controllers": [], "strip_mappings": {}}
        return cls(strips or [], mapping)

    def strip_names(self) -> list[str]:
        return list(self._strips.keys())

    def info(self, strip_name: str) -> Optional[dict[str, Any]]:
        s = self._strips.get(strip_name)
        if not s:
            return None
        return {
            "name": s.name,
            "total_mm": s.total_mm,
            "marker_spacing_mm": s.marker_spacing_mm,
            "num_markers": s.num_markers(),
            "controller_ip": s.controller_ip,
            "start_universe": s.start_universe,
            "num_universes": s.num_universes,
            "reverse": s.reverse,
        }

    def marker_position_mm(self, strip_name: str, marker_idx: int) -> Optional[tuple[float, float]]:
        s = self._strips.get(strip_name)
        if not s:
            return None
        n = s.num_markers()
        if marker_idx < 0 or marker_idx >= n:
            return None
        return s.marker_position_mm(marker_idx)

    def light_marker(
        self,
        strip_name: str,
        marker_idx: int,
        rgbw: tuple[int, int, int, int] = (0, 0, 0, 255),
    ) -> dict[str, Any]:
        """Light marker idx, all other LEDs on this strip off. Returns status."""
        s = self._strips.get(strip_name)
        if not s:
            return {"ok": False, "error": f"unknown strip: {strip_name}"}

        # Effective pixel index respecting reverse mapping
        total_pixels = s.num_universes * PIXELS_PER_UNIVERSE
        wire_idx = marker_idx
        if s.reverse:
            # Reverse along the WIRING order (logical pixels run backwards).
            # marker_idx is "from start of physical strip", so we flip it.
            wire_idx = total_pixels - 1 - marker_idx

        sent_universes: list[int] = []
        # Build & send one packet per universe of this strip
        for u_offset in range(s.num_universes):
            uni = s.start_universe + u_offset
            data = bytearray(CHANNELS_PER_UNIVERSE)
            pixel_lo = u_offset * PIXELS_PER_UNIVERSE
            pixel_hi = pixel_lo + PIXELS_PER_UNIVERSE
            if pixel_lo <= wire_idx < pixel_hi:
                # start_channel is 1-based; pixel 0 starts at start_channel
                ch_offset = (s.start_channel - 1) + (wire_idx - pixel_lo) * CHANNELS_PER_PIXEL
                if ch_offset + CHANNELS_PER_PIXEL <= CHANNELS_PER_UNIVERSE:
                    data[ch_offset:ch_offset + CHANNELS_PER_PIXEL] = bytes(rgbw)
            self._sock.sendto(_make_artdmx(uni, bytes(data)), (s.controller_ip, ARTNET_PORT))
            sent_universes.append(uni)

        return {
            "ok": True,
            "strip": strip_name,
            "marker_idx": marker_idx,
            "wire_idx": wire_idx,
            "universes_sent": sent_universes,
            "controller_ip": s.controller_ip,
        }

    def _send_strip_pattern(
        self,
        strip: StripGeom,
        on_indices: set[int],
        rgbw: tuple[int, int, int, int],
    ) -> None:
        """Exactly like meter_test_gui.send_channels: build one flat channel list
        for the whole strip, then split it into universe-sized chunks and emit
        one Art-Net packet per universe (no skipping)."""
        # reverse maps physical marker N → DMX index (n_markers - 1 - N).
        # The strip's actual logical-pixel count drives the reverse, not the
        # universe capacity (which usually overshoots).
        n_markers = strip.num_markers()
        total_channels = strip.num_universes * CHANNELS_PER_UNIVERSE
        channels = bytearray(total_channels)
        for marker_idx in on_indices:
            wire_idx = n_markers - 1 - marker_idx if strip.reverse else marker_idx
            if not (0 <= wire_idx < n_markers):
                continue
            ch = (strip.start_channel - 1) + wire_idx * CHANNELS_PER_PIXEL
            if ch + CHANNELS_PER_PIXEL > total_channels:
                continue
            channels[ch:ch + CHANNELS_PER_PIXEL] = bytes(rgbw)
        # Send one packet per universe regardless of content (empty universes also
        # need zeros to actively turn LEDs off, matching meter_test_gui behaviour).
        for u_offset in range(strip.num_universes):
            uni = strip.start_universe + u_offset
            data = bytes(
                channels[u_offset * CHANNELS_PER_UNIVERSE:(u_offset + 1) * CHANNELS_PER_UNIVERSE]
            )
            self._sock.sendto(_make_artdmx(uni, data), (strip.controller_ip, ARTNET_PORT))

    def light_dotted(
        self,
        stride: int = 2,
        rgbw: tuple[int, int, int, int] = (0, 0, 0, 255),
        strip_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Single-marker stride (legacy)."""
        targets = strip_names or list(self._strips.keys())
        result: dict[str, list[int]] = {}
        for name in targets:
            s = self._strips.get(name)
            if not s:
                continue
            n_markers = s.num_markers()
            on_indices = list(range(0, n_markers, max(1, int(stride))))
            self._send_strip_pattern(s, set(on_indices), rgbw)
            result[name] = on_indices
        return {"ok": True, "stride": stride, "rgbw": list(rgbw), "lit_per_strip": result}

    def light_blocks(
        self,
        on_markers: int = 5,
        off_markers: int = 5,
        rgbw: tuple[int, int, int, int] = (0, 0, 0, 255),
        strip_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Light continuous blocks of N markers (50 cm at default 5).
        Pattern: on_markers × 10cm lit, then off_markers × 10cm dark, repeat.
        Each marker = 1 logical pixel = 6 LEDs = 10 cm."""
        on_n = max(1, int(on_markers))
        off_n = max(0, int(off_markers))
        cycle = on_n + off_n if (on_n + off_n) > 0 else 1
        targets = strip_names or list(self._strips.keys())
        result: dict[str, list[int]] = {}
        for name in targets:
            s = self._strips.get(name)
            if not s:
                continue
            n_markers = s.num_markers()
            on_indices = [i for i in range(n_markers) if (i % cycle) < on_n]
            self._send_strip_pattern(s, set(on_indices), rgbw)
            result[name] = on_indices
        return {
            "ok": True,
            "on_markers": on_n, "off_markers": off_n,
            "rgbw": list(rgbw),
            "lit_per_strip": result,
        }

    def light_all_markers(
        self,
        rgbw: tuple[int, int, int, int] = (0, 0, 0, 255),
        strip_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Light every marker (continuous illumination)."""
        return self.light_dotted(stride=1, rgbw=rgbw, strip_names=strip_names)

    def all_off(self, strip_name: Optional[str] = None) -> dict[str, Any]:
        """Turn off all LEDs on one strip (or all strips if name is None)."""
        targets = [strip_name] if strip_name else list(self._strips.keys())
        sent: list[str] = []
        for name in targets:
            s = self._strips.get(name)
            if not s:
                continue
            for u_offset in range(s.num_universes):
                uni = s.start_universe + u_offset
                self._sock.sendto(
                    _make_artdmx(uni, bytes(CHANNELS_PER_UNIVERSE)),
                    (s.controller_ip, ARTNET_PORT),
                )
            sent.append(name)
        return {"ok": True, "cleared": sent}
