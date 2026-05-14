#!/usr/bin/env python3
"""GDPR erasure: hard-delete an identity and its biometric data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._common import load_gallery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--id", required=True, help="identity_id (UUID) to erase")
    ap.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    args = ap.parse_args()

    if not args.yes:
        sys.stderr.write(f"Hard-delete identity {args.id}? Type 'yes' to confirm: ")
        sys.stderr.flush()
        confirm = sys.stdin.readline().strip().lower()
        if confirm != "yes":
            print("[delete] aborted", file=sys.stderr)
            return 1

    gallery, _ = load_gallery(args.config)
    try:
        info = gallery.delete_identity(args.id)
    finally:
        gallery.close()
    print(f"[delete] erased identity {args.id}: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
