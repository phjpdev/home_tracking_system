#!/usr/bin/env python3
"""List enrolled identities and their consent state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ._common import load_gallery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    gallery, _ = load_gallery(args.config)
    try:
        rows = gallery.list_identities()
    finally:
        gallery.close()

    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    if not rows:
        print("(no enrolled identities)")
        return 0
    print(f"{'name':24s}  {'identity_id':38s}  {'faces':>5s}  {'consent':24s}")
    for r in rows:
        consent = "revoked" if r.get("revoked_at") else str(r.get("lawful_basis") or "-")
        print(
            f"{(r.get('display_name') or '')[:24]:24s}  "
            f"{str(r.get('identity_id') or ''):38s}  "
            f"{int(r.get('face_count') or 0):>5d}  "
            f"{consent:24s}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
