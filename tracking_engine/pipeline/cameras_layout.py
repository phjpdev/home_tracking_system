"""Load camera placement metadata from ``camera_placement_plan`` JSON exports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class LayoutCamera:
    """One physical camera row from ``cameras_config.json``."""

    name: str
    numeric_id: int
    room: str
    role: str


def load_cameras_layout(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "cameras" not in data or not isinstance(data["cameras"], list):
        raise ValueError(f"{path}: missing non-empty 'cameras' array")
    return data


def layout_cameras_by_name(layout: Mapping[str, Any]) -> dict[str, LayoutCamera]:
    out: dict[str, LayoutCamera] = {}
    for row in layout["cameras"]:
        name = str(row["name"])
        out[name] = LayoutCamera(
            name=name,
            numeric_id=int(row["id"]),
            room=str(row["room"]),
            role=str(row.get("role", "")),
        )
    return out


def resolve_active_streams(
    layout: Mapping[str, Any],
    streams_yaml: list[dict[str, Any]],
    video_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Merge placement JSON with YAML ``streams`` entries.

    Each YAML row: ``name``, ``enabled`` (default true), ``rtsp_url`` (optional if video override).

    Returns ordered list of dicts:
    ``name, room, zone, rtsp_url, video_path`` (video_path may be None).
    """

    by_name = layout_cameras_by_name(layout)
    overrides = dict(video_overrides or {})
    active: list[dict[str, Any]] = []

    for row in streams_yaml:
        if not row:
            continue
        if not bool(row.get("enabled", True)):
            continue
        name = str(row["name"])
        if name not in by_name:
            raise KeyError(
                f"camera name {name!r} not found in layout "
                f"(expected one of {sorted(by_name)!r})"
            )
        lc = by_name[name]
        video_path = overrides.pop(name, None)
        rtsp_url = str(row.get("rtsp_url", "") or "").strip()
        if video_path:
            rtsp_url = ""
        elif not rtsp_url:
            raise ValueError(
                f"stream {name!r}: set rtsp_url in config or pass --video {name}=path"
            )

        active.append(
            {
                "name": lc.name,
                "numeric_id": lc.numeric_id,
                "room": lc.room,
                "zone": lc.room,
                "rtsp_url": rtsp_url,
                "video_path": video_path,
            }
        )

    if overrides:
        lines: list[str] = []
        for n in sorted(overrides.keys()):
            if n not in by_name:
                lines.append(f"{n}: not in layout JSON")
                continue
            row = next((r for r in streams_yaml if r and str(r.get("name")) == n), None)
            if row is None:
                lines.append(f"{n}: add a multi_camera.streams row with this name")
            elif not bool(row.get("enabled", True)):
                lines.append(f"{n}: stream disabled in YAML (set enabled: true)")
            else:
                lines.append(f"{n}: unused override")

        raise ValueError(
            "--video names must match enabled streams:\n  " + "\n  ".join(lines)
        )

    return active
