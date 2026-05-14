#!/usr/bin/env python3
"""Mark an identity's consent as revoked (purge runs nightly)."""

from __future__ import annotations

import argparse
from pathlib import Path

from ._common import load_gallery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--id", required=True)
    args = ap.parse_args()

    gallery, _ = load_gallery(args.config)
    try:
        gallery.revoke_consent(args.id)
    finally:
        gallery.close()
    print(f"[revoke] consent revoked for {args.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
