"""Smoke test: confirm the SQLite gallery schema matches what the plan
expects so future migrations can rely on it.

Run directly:

    python -m tracking_engine.reid.tests.test_gallery_schema

Or via pytest:

    pytest tracking_engine/reid/tests/

The test creates a temporary on-disk database (FAISS is required) and
checks every table + selected columns. It will skip gracefully when
``faiss`` is not importable so it can run on hosts without the wheel.
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
import tempfile
from pathlib import Path

REQUIRED_TABLES: dict[str, set[str]] = {
    "model_registry": {"model_id", "modality", "dimension", "metric", "deprecated", "created_at"},
    "identity": {"identity_id", "tenant_id", "site_id", "display_name", "status", "enrolled"},
    "global_track": {"global_track_id", "tenant_id", "identity_id", "status", "first_seen", "last_seen"},
    "appearance_embedding": {
        "id", "global_track_id", "identity_id", "embedding", "model_id",
        "quality", "cam_id", "local_track_label", "frame_ts", "metadata", "created_at",
    },
    "fusion_event": {
        "id", "ts", "event_type", "global_track_id", "identity_id",
        "body_score", "face_score", "threshold_snapshot", "model_ids",
    },
}

PHASE_D_TABLES: dict[str, set[str]] = {
    "face_embedding": {"id", "identity_id", "embedding", "model_id", "quality", "enrolled", "created_at"},
    "consent_record": {"identity_id", "lawful_basis", "granted_at", "revoked_at", "retention_days"},
}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _run() -> int:
    try:
        importlib.import_module("faiss")
    except ImportError:
        print("[skip] faiss not installed; skipping gallery schema test", file=sys.stderr)
        return 0

    from tracking_engine.reid.config import ReidConfig
    from tracking_engine.reid.gallery_sqlite import GallerySqliteFaiss

    # ``ignore_cleanup_errors`` handles Windows SQLite WAL file lingering.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cfg = ReidConfig.from_cfg(
            {
                "reid": {
                    "enabled": True,
                    "backend": "sqlite_faiss",
                    "dsn": f"sqlite:///{Path(tmp) / 'reid_test.db'}",
                    "faiss_path": str(Path(tmp) / "reid_test.faiss"),
                    "tenant_id": "test",
                    "body": {"model_id": "osnet_x025_v1", "dimension": 512},
                }
            },
            cfg_dir=Path(tmp),
        )
        gallery = GallerySqliteFaiss(cfg)
        try:
            with sqlite3.connect(str(cfg.sqlite_path)) as conn:
                missing: list[str] = []
                for table, expected_cols in REQUIRED_TABLES.items():
                    got = _columns(conn, table)
                    if not got:
                        missing.append(f"missing table: {table}")
                        continue
                    leftover = expected_cols - got
                    if leftover:
                        missing.append(f"{table} missing columns: {sorted(leftover)}")
                for table, expected_cols in PHASE_D_TABLES.items():
                    got = _columns(conn, table)
                    if not got:
                        missing.append(f"missing table: {table}")
                        continue
                    leftover = expected_cols - got
                    if leftover:
                        missing.append(f"{table} missing columns: {sorted(leftover)}")

                if missing:
                    print("[fail] schema mismatch:", file=sys.stderr)
                    for m in missing:
                        print(f"  - {m}", file=sys.stderr)
                    return 1
        finally:
            gallery.close()

    print("[ok] gallery schema matches the plan")
    return 0


def test_gallery_schema_matches_plan() -> None:
    assert _run() == 0


if __name__ == "__main__":
    raise SystemExit(_run())
