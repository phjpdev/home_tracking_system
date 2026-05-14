#!/usr/bin/env python3
"""Split a global_track after a timestamp.

Use when a global_track was incorrectly merged with a new person (e.g.
two people swapped places in front of one camera and the tracker carried
the wrong ID). All appearance_embedding rows for the chosen global_track
with ``frame_ts >= --since`` are detached and re-assigned to a brand-new
``tentative`` global_track.
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
    ap.add_argument("--gid", required=True, help="global_track_id (UUID) to split")
    ap.add_argument("--since", type=float, required=True, help="unix timestamp; rows at or after are moved")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    if not args.yes:
        sys.stderr.write(
            f"Split {args.gid} since {args.since}? Type 'yes' to confirm: "
        )
        sys.stderr.flush()
        confirm = sys.stdin.readline().strip().lower()
        if confirm != "yes":
            print("[split] aborted", file=sys.stderr)
            return 1

    gallery, rcfg = load_gallery(args.config)
    try:
        new_gid = gallery.create_global_track(
            tenant_id=rcfg.tenant_id, status="tentative", ts=time.time()
        )
        conn = gallery._conn  # pylint: disable=protected-access
        with gallery._lock:   # pylint: disable=protected-access
            n = conn.execute(
                """
                UPDATE appearance_embedding
                SET global_track_id = ?
                WHERE global_track_id = ? AND frame_ts >= ?
                """,
                (new_gid, args.gid, args.since),
            ).rowcount
            conn.execute(
                """
                INSERT INTO fusion_event(ts, event_type, global_track_id, identity_id,
                                         body_score, face_score, threshold_snapshot, model_ids)
                VALUES (?, 'split', ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    time.time(),
                    args.gid,
                    f'{{"new_global_track_id":"{new_gid}","since":{args.since}}}',
                    "{}",
                ),
            )
            conn.commit()
            gallery._rebuild_body_index_locked()  # pylint: disable=protected-access
    finally:
        gallery.close()

    print(f"[split] moved {n} appearance rows from {args.gid} -> {new_gid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
