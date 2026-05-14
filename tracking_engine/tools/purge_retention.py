#!/usr/bin/env python3
"""GDPR retention enforcement.

Run nightly. Two effects:

1. Hard-deletes every identity whose ``consent_record.revoked_at`` is set.
2. Drops ``appearance_embedding`` and ``sighting`` rows older than
   ``--retention-days`` that are not linked to an enrolled identity.

The aim is that the on-disk database never holds embeddings without an
active consent record or a fresh anonymous sighting.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ._common import load_gallery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--retention-days", type=int, default=90)
    args = ap.parse_args()

    cutoff = time.time() - float(args.retention_days) * 86400.0

    gallery, _ = load_gallery(args.config)
    try:
        conn = gallery._conn  # pylint: disable=protected-access
        with gallery._lock:   # pylint: disable=protected-access
            revoked_rows = conn.execute(
                "SELECT identity_id FROM consent_record WHERE revoked_at IS NOT NULL"
            ).fetchall()
            revoked_ids = [r["identity_id"] for r in revoked_rows]
        deleted_identities = 0
        for rid in revoked_ids:
            gallery.delete_identity(rid)
            deleted_identities += 1

        with gallery._lock:   # pylint: disable=protected-access
            cur = conn.cursor()
            n_app = cur.execute(
                """
                DELETE FROM appearance_embedding
                WHERE (identity_id IS NULL)
                  AND (frame_ts IS NULL OR frame_ts < ?)
                """,
                (cutoff,),
            ).rowcount
            n_sight = cur.execute(
                "DELETE FROM sighting WHERE started_ts < ?",
                (cutoff,),
            ).rowcount
            conn.commit()
            gallery._rebuild_body_index_locked()  # pylint: disable=protected-access
    finally:
        gallery.close()

    print(
        f"[purge] revoked_identities_deleted={deleted_identities} "
        f"orphan_appearances_deleted={n_app} sightings_deleted={n_sight}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
