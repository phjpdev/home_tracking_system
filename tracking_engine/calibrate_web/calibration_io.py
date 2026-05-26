"""Atomic merge into ``camera_calibrations.json``.

The schema is intentionally identical to what
:func:`tools.calibrate_homography._merge_into_json` writes, so the
runtime loader in
:mod:`tracking_engine.pipeline.homography` consumes either source
without modification. Cameras absent from ``new_entries`` keep their
existing JSON entry untouched, so a partial recalibration is safe.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_CALIB_PATH = Path("tracking_engine/calibration/camera_calibrations.json")


def read_calibrations(path: Path) -> dict[str, Any]:
    """Return the parsed JSON, or empty dict if the file is missing."""

    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_merge_calibrations(
    path: Path, new_entries: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Merge ``new_entries`` into the calibration JSON via tempfile+rename.

    Parameters
    ----------
    path
        Target ``camera_calibrations.json`` (created if missing).
    new_entries
        ``{cam_id: entry}`` to write. Cameras already in the file but
        absent from ``new_entries`` are preserved verbatim.

    Returns
    -------
    The merged dict that was actually written. Useful for tests.
    """

    existing = read_calibrations(path)
    existing.update(new_entries)

    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(existing, indent=2, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(serialized)
        tmp.flush()
        try:
            os.fsync(tmp.fileno())
        except OSError:  # pragma: no cover - non-POSIX best-effort
            pass
        tmp_name = tmp.name

    os.replace(tmp_name, str(path))
    return existing
