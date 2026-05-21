#!/usr/bin/env python3
"""Print a one-shot health snapshot of the SQLite + FAISS gallery.

Useful answer to the question "how do I check the database?". Reports:

- file paths and sizes for the SQLite DB and FAISS sidecar.
- per-table row counts.
- enrolled identities (name + faces + consent).
- 10 most recent global tracks with state, identity link and timestamps.
- 10 most recent fusion events.
- FAISS index sizes (body + face).

Run from repo root:

.. code-block:: bash

   python -m tracking_engine.tools.inspect_gallery
   python -m tracking_engine.tools.inspect_gallery --json
   python -m tracking_engine.tools.inspect_gallery --since 1h    # only recent rows
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from ._common import load_gallery


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)


def _parse_since(spec: str | None) -> float | None:
    if not spec:
        return None
    m = _DURATION_RE.match(spec)
    if not m:
        try:
            return float(spec)
        except ValueError as exc:
            raise SystemExit(f"could not parse --since {spec!r}") from exc
    value = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return time.time() - value * multipliers[unit]


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        name = r[0] if not isinstance(r, sqlite3.Row) else r["name"]
        if name.startswith("sqlite_"):
            continue
        c = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        out[name] = int(c)
    return out


def _recent_tracks(conn: sqlite3.Connection, since_ts: float | None, limit: int) -> list[dict[str, Any]]:
    sql = (
        "SELECT g.global_track_id, g.status, g.first_seen, g.last_seen, "
        "       g.identity_id, i.display_name, "
        "       (SELECT COUNT(*) FROM appearance_embedding ae "
        "        WHERE ae.global_track_id = g.global_track_id) AS proto_count "
        "FROM global_track g LEFT JOIN identity i ON i.identity_id = g.identity_id "
    )
    args: list[Any] = []
    if since_ts is not None:
        sql += "WHERE g.last_seen >= ? "
        args.append(since_ts)
    sql += "ORDER BY g.last_seen DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "global_track_id": r["global_track_id"],
                "status": r["status"],
                "first_seen": float(r["first_seen"]),
                "last_seen": float(r["last_seen"]),
                "identity_id": r["identity_id"],
                "display_name": r["display_name"],
                "prototype_count": int(r["proto_count"] or 0),
            }
        )
    return out


def _recent_events(conn: sqlite3.Connection, since_ts: float | None, limit: int) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, ts, event_type, global_track_id, identity_id, body_score, face_score "
        "FROM fusion_event "
    )
    args: list[Any] = []
    if since_ts is not None:
        sql += "WHERE ts >= ? "
        args.append(since_ts)
    sql += "ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def _file_size(path: Path) -> str:
    if not path.is_file():
        return "(missing)"
    size = path.stat().st_size
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _ts_to_str(t: float | None) -> str:
    if t is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(t)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=Path("tracking_engine/config.multi_camera.yaml"))
    ap.add_argument("--since", default=None, help="filter recent rows newer than e.g. 30m / 24h / 7d")
    ap.add_argument("--limit", type=int, default=10, help="rows per recent section")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a human report")
    args = ap.parse_args()

    if not args.config.is_file():
        print(f"[inspect] config not found: {args.config}", file=sys.stderr)
        return 2

    since_ts = _parse_since(args.since)
    gallery, rcfg = load_gallery(args.config)
    try:
        conn = gallery._conn  # noqa: SLF001 — single-process inspector reuses live conn
        counts = _table_counts(conn)
        identities = gallery.list_identities()
        tracks = _recent_tracks(conn, since_ts, args.limit)
        events = _recent_events(conn, since_ts, args.limit)
        body_index = int(gallery._index.ntotal)  # noqa: SLF001
        face_index = int(gallery._face_index.ntotal)  # noqa: SLF001
    finally:
        gallery.close()

    sqlite_path = Path(rcfg.sqlite_path)
    faiss_path = Path(rcfg.faiss_path)

    if args.json:
        out = {
            "config": str(args.config.resolve()),
            "sqlite_path": str(sqlite_path),
            "sqlite_size": _file_size(sqlite_path),
            "faiss_path": str(faiss_path),
            "faiss_size": _file_size(faiss_path),
            "tenant_id": rcfg.tenant_id,
            "since_ts": since_ts,
            "table_counts": counts,
            "faiss_body_ntotal": body_index,
            "faiss_face_ntotal": face_index,
            "identities": identities,
            "recent_tracks": tracks,
            "recent_events": events,
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"config:        {args.config.resolve()}")
    print(f"sqlite:        {sqlite_path}  ({_file_size(sqlite_path)})")
    print(f"faiss:         {faiss_path}  ({_file_size(faiss_path)})")
    print(f"tenant:        {rcfg.tenant_id}")
    print(f"body model:    {rcfg.body_model_id}  dim={rcfg.body_dimension}  threshold_match={rcfg.threshold_match}")
    if rcfg.face_enabled:
        print(f"face model:    {rcfg.face_model_id}  dim={rcfg.face_dimension}  threshold_high={rcfg.face_threshold_high}")
    else:
        print("face:          disabled")
    print(f"faiss vectors: body={body_index}  face={face_index}")
    print()

    print("table row counts:")
    for name, count in counts.items():
        print(f"  {name:30s}  {count:>8d}")
    print()

    print(f"identities ({len(identities)}):")
    if not identities:
        print("  (none enrolled)")
    else:
        for r in identities:
            consent = "revoked" if r.get("revoked_at") else str(r.get("lawful_basis") or "-")
            print(
                f"  {(r.get('display_name') or '')[:24]:24s}  "
                f"{str(r.get('identity_id') or ''):38s}  "
                f"faces={int(r.get('face_count') or 0):>3d}  "
                f"{consent:20s}"
            )
    print()

    suffix = f" since {args.since}" if args.since else ""
    print(f"recent global tracks{suffix} (top {args.limit}):")
    if not tracks:
        print("  (none)")
    else:
        for t in tracks:
            name = t.get("display_name") or "-"
            print(
                f"  {t['global_track_id'][:8]}…  status={t['status']:18s}  "
                f"protos={t['prototype_count']:>3d}  "
                f"last={_ts_to_str(t['last_seen'])}  identity={name}"
            )
    print()

    print(f"recent fusion events{suffix} (top {args.limit}):")
    if not events:
        print("  (none)")
    else:
        for e in events:
            gid = (e.get("global_track_id") or "-")[:8]
            iid = (e.get("identity_id") or "-")[:8]
            body = e.get("body_score")
            face = e.get("face_score")
            body_s = f"{body:.3f}" if isinstance(body, (int, float)) else "-"
            face_s = f"{face:.3f}" if isinstance(face, (int, float)) else "-"
            print(
                f"  {_ts_to_str(e['ts'])}  type={e['event_type']:14s}  "
                f"gid={gid}…  identity={iid}…  body={body_s}  face={face_s}"
            )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
