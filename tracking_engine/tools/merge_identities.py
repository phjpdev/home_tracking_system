#!/usr/bin/env python3
"""Manually merge one identity into another.

Used when the operator notices that the same person was enrolled twice
(e.g. once on each camera). All face + appearance + global_track links
from ``--from`` are re-pointed at ``--into``; the source identity is
then deleted.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ._common import load_gallery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--from", dest="src", required=True, help="identity to merge FROM (will be deleted)")
    ap.add_argument("--into", dest="dst", required=True, help="identity to merge INTO (kept)")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    if args.src == args.dst:
        print("[merge] --from and --into are equal", file=sys.stderr)
        return 1
    if not args.yes:
        sys.stderr.write(f"Merge {args.src} -> {args.dst}? Type 'yes' to confirm: ")
        sys.stderr.flush()
        confirm = sys.stdin.readline().strip().lower()
        if confirm != "yes":
            print("[merge] aborted", file=sys.stderr)
            return 1

    gallery, _ = load_gallery(args.config)
    try:
        conn = gallery._conn  # pylint: disable=protected-access
        with gallery._lock:   # pylint: disable=protected-access
            ts = time.time()
            n_face = conn.execute(
                "UPDATE face_embedding SET identity_id = ? WHERE identity_id = ?",
                (args.dst, args.src),
            ).rowcount
            n_app = conn.execute(
                "UPDATE appearance_embedding SET identity_id = ? WHERE identity_id = ?",
                (args.dst, args.src),
            ).rowcount
            n_track = conn.execute(
                "UPDATE global_track SET identity_id = ? WHERE identity_id = ?",
                (args.dst, args.src),
            ).rowcount
            conn.execute(
                """
                INSERT INTO fusion_event(ts, event_type, global_track_id, identity_id,
                                         body_score, face_score, threshold_snapshot, model_ids)
                VALUES (?, 'merge', NULL, ?, NULL, NULL, ?, ?)
                """,
                (
                    ts,
                    args.dst,
                    f'{{"merged_from":"{args.src}","into":"{args.dst}"}}',
                    "{}",
                ),
            )
            # face_embedding moved; remove the now-orphaned source identity (cascade clears any residue).
            conn.execute("DELETE FROM identity WHERE identity_id = ?", (args.src,))
            conn.commit()
            gallery._rebuild_face_index_locked()  # pylint: disable=protected-access
    finally:
        gallery.close()

    print(f"[merge] {args.src} -> {args.dst}: faces={n_face} appearances={n_app} tracks={n_track}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
