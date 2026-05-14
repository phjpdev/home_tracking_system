"""SQLite relational store plus FAISS inner-product search over L2-normalized body embeddings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .config import ReidConfig


@dataclass(frozen=True)
class BodyNeighbor:
    distance: float
    similarity: float
    appearance_id: int
    global_track_id: Optional[str]


class GallerySqliteFaiss:
    def __init__(self, cfg: ReidConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        import faiss

        self._faiss = faiss  # pylint: disable=attribute-defined-outside-init

        self._sqlite_path = str(cfg.sqlite_path)
        self._dim = cfg.body_dimension
        self._conn = sqlite3.connect(self._sqlite_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        self._register_body_model(cfg.body_model_id, cfg.body_dimension)

        base = faiss.IndexFlatIP(self._dim)
        self._index = faiss.IndexIDMap2(base)

        self._rebuild_body_index_locked()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_registry (
              model_id TEXT PRIMARY KEY,
              modality TEXT NOT NULL,
              dimension INTEGER NOT NULL,
              metric TEXT NOT NULL DEFAULT 'cosine',
              deprecated INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS identity (
              identity_id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              site_id TEXT,
              display_name TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              enrolled INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS global_track (
              global_track_id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              identity_id TEXT,
              status TEXT NOT NULL,
              first_seen REAL NOT NULL,
              last_seen REAL NOT NULL,
              FOREIGN KEY (identity_id) REFERENCES identity (identity_id)
            );

            CREATE TABLE IF NOT EXISTS appearance_embedding (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              global_track_id TEXT NOT NULL,
              identity_id TEXT,
              embedding BLOB NOT NULL,
              model_id TEXT NOT NULL,
              quality REAL,
              cam_id TEXT,
              local_track_label TEXT,
              frame_ts REAL,
              metadata TEXT,
              created_at REAL NOT NULL,
              FOREIGN KEY (global_track_id) REFERENCES global_track (global_track_id),
              FOREIGN KEY (identity_id) REFERENCES identity (identity_id),
              FOREIGN KEY (model_id) REFERENCES model_registry (model_id)
            );

            CREATE INDEX IF NOT EXISTS appearance_embedding_model_gid
              ON appearance_embedding (model_id, global_track_id);

            CREATE TABLE IF NOT EXISTS fusion_event (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts REAL NOT NULL,
              event_type TEXT NOT NULL,
              global_track_id TEXT,
              identity_id TEXT,
              body_score REAL,
              face_score REAL,
              threshold_snapshot TEXT NOT NULL,
              model_ids TEXT NOT NULL,
              FOREIGN KEY (identity_id) REFERENCES identity (identity_id)
            );
            """
        )
        self._conn.commit()

    def _register_body_model(self, model_id: str, dimension: int) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO model_registry(model_id, modality, dimension, metric, deprecated, created_at)
            VALUES (?, 'body', ?, 'cosine', 0, ?)
            """,
            (model_id, dimension, now),
        )
        self._conn.commit()

    def _rebuild_body_index_locked(self) -> None:
        self._index.reset()
        model_id = self.cfg.body_model_id
        rows = self._conn.execute(
            """
            SELECT id, embedding FROM appearance_embedding
            WHERE model_id = ? AND embedding IS NOT NULL
            ORDER BY id
            """,
            (model_id,),
        ).fetchall()

        import faiss

        if not rows:
            return

        mats: list[np.ndarray] = []
        ids: list[int] = []
        for r in rows:
            vid = int(r["id"])
            blob = r["embedding"]
            v = np.frombuffer(blob, dtype=np.float32).reshape(1, -1).copy()
            if v.shape[1] != self._dim:
                raise ValueError(
                    f"stored embedding dim {v.shape[1]} != configured {self._dim} for appearance id {vid}"
                )
            faiss.normalize_L2(v)
            mats.append(v)
            ids.append(vid)

        x = np.vstack(mats)
        ids_arr = np.array(ids, dtype=np.int64)
        self._index.add_with_ids(x, ids_arr)

    def rebuild_body_index(self) -> None:
        with self._lock:
            self._rebuild_body_index_locked()

    def create_global_track(
        self,
        *,
        tenant_id: str,
        status: str = "tentative",
        ts: float,
    ) -> str:
        gid = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO global_track(global_track_id, tenant_id, identity_id, status, first_seen, last_seen)
                VALUES (?, ?, NULL, ?, ?, ?)
                """,
                (gid, tenant_id, status, ts, ts),
            )
            self._conn.commit()
        return gid

    def touch_global_track(self, global_track_id: str, ts: float, status: Optional[str] = None) -> None:
        with self._lock:
            if status is None:
                self._conn.execute(
                    "UPDATE global_track SET last_seen = ? WHERE global_track_id = ?",
                    (ts, global_track_id),
                )
            else:
                self._conn.execute(
                    "UPDATE global_track SET last_seen = ?, status = ? WHERE global_track_id = ?",
                    (ts, status, global_track_id),
                )
            self._conn.commit()

    def insert_appearance(
        self,
        *,
        global_track_id: str,
        embedding: np.ndarray,
        quality: Optional[float],
        cam_id: str,
        local_track_label: str,
        frame_ts: float,
    ) -> int:
        if embedding.ndim != 1 or embedding.shape[0] != self._dim:
            raise ValueError("embedding vector shape mismatch")

        blob = embedding.astype(np.float32, copy=False).tobytes()
        now = time.time()
        meta = "{}"
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO appearance_embedding(
                  global_track_id, identity_id, embedding, model_id, quality,
                  cam_id, local_track_label, frame_ts, metadata, created_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    global_track_id,
                    blob,
                    self.cfg.body_model_id,
                    quality,
                    cam_id,
                    local_track_label,
                    frame_ts,
                    meta,
                    now,
                ),
            )
            row_id = int(cur.lastrowid)
            v = embedding.astype(np.float32, copy=False).reshape(1, -1).copy()
            self._faiss.normalize_L2(v)
            self._index.add_with_ids(v, np.array([row_id], dtype=np.int64))
            self._conn.commit()
        return row_id

    def count_prototypes_global(self, global_track_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS c FROM appearance_embedding
                WHERE global_track_id = ? AND model_id = ?
                """,
                (global_track_id, self.cfg.body_model_id),
            ).fetchone()
            return int(row["c"])

    def min_distance_to_own_prototypes(self, global_track_id: str, vec_flat: np.ndarray) -> Optional[float]:
        """Minimum cosine distance to existing protos of this global track (excluding full gallery search)."""
        with self._lock:
            blobs = [
                r[0]
                for r in self._conn.execute(
                    """
                    SELECT embedding FROM appearance_embedding
                    WHERE global_track_id = ? AND model_id = ?
                    """,
                    (global_track_id, self.cfg.body_model_id),
                ).fetchall()
            ]
            if not blobs:
                return None
            v = vec_flat.astype(np.float32).reshape(1, -1).copy()
            self._faiss.normalize_L2(v)
            sims = []
            for blob in blobs:
                p = np.frombuffer(blob, dtype=np.float32).reshape(1, -1).copy()
                self._faiss.normalize_L2(p)
                sims.append(float(np.dot(v.ravel(), p.ravel())))
            best = max(sims)
            return 1.0 - best

    def query_body(self, vec: np.ndarray, k: int) -> list[BodyNeighbor]:
        if vec.ndim != 1:
            vec = vec.reshape(-1)
        q = vec.astype(np.float32).reshape(1, -1).copy()
        self._faiss.normalize_L2(q)

        with self._lock:
            if self._index.ntotal == 0:
                return []

            n_probe = max(1, min(256, max(self._index.ntotal, self._dim)))
            probe = max(k + 64, min(n_probe, k + max(128, self._index.ntotal or 128)))
            sims, ap_ids = self._index.search(q, probe)
            sim_row = sims[0].tolist()
            id_row = ap_ids[0].tolist()

            grouped: dict[str, tuple[float, int]] = {}
            for sim, rid in zip(sim_row, id_row):
                rid_i = int(rid)
                if rid_i < 0:
                    continue
                sim_f = float(np.clip(sim, -1.0, 1.0))
                dist = max(0.0, min(2.0, 1.0 - sim_f))
                row = self._conn.execute(
                    """
                    SELECT id, global_track_id FROM appearance_embedding WHERE id = ?
                    """,
                    (rid_i,),
                ).fetchone()
                if row is None:
                    continue
                gid = row["global_track_id"]
                if gid is None:
                    continue
                prev = grouped.get(str(gid))
                if prev is None or dist < prev[0]:
                    grouped[str(gid)] = (dist, rid_i)

        out: list[BodyNeighbor] = []
        for gid_str, pair in grouped.items():
            dist, rid = pair
            out.append(
                BodyNeighbor(
                    distance=dist,
                    similarity=max(-1.0, min(1.0, 1.0 - dist)),
                    appearance_id=int(rid),
                    global_track_id=str(gid_str),
                )
            )

        out.sort(key=lambda x: x.distance)
        return out[:k]

    def link_track_to_identity(
        self,
        global_track_id: str,
        identity_id: str,
        *,
        tenant_id: str,
        scores: Optional[dict[str, float]] = None,
    ) -> None:
        # Phase C hook — placeholders keep schema compatible.
        ts = time.time()
        scores = scores or {}
        with self._lock:
            exists = self._conn.execute(
                "SELECT identity_id FROM global_track WHERE global_track_id = ?",
                (global_track_id,),
            ).fetchone()
            if exists is None:
                return

            identity_row = self._conn.execute(
                "SELECT 1 FROM identity WHERE identity_id = ? AND tenant_id = ? LIMIT 1",
                (identity_id, tenant_id),
            ).fetchone()
            if identity_row is None:
                return

            self._conn.execute(
                """
                UPDATE global_track SET identity_id = ?, status = ?, last_seen = ?
                WHERE global_track_id = ?
                """,
                (identity_id, "linked", ts, global_track_id),
            )

            snapshot = {"scores": scores, "note": "link_track_to_identity"}
            models = {}
            self._conn.execute(
                """
                INSERT INTO fusion_event(ts, event_type, global_track_id, identity_id, body_score, face_score, threshold_snapshot, model_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    "link",
                    global_track_id,
                    identity_id,
                    scores.get("body"),
                    scores.get("face"),
                    json.dumps(snapshot),
                    json.dumps(models),
                ),
            )
            self._conn.commit()

    def log_fusion_event(
        self,
        *,
        event_type: str,
        global_track_id: Optional[str],
        identity_id: Optional[str],
        body_score: Optional[float],
        face_score: Optional[float],
        threshold_snapshot: dict[str, Any],
        model_ids: dict[str, str],
    ) -> None:
        ts = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO fusion_event(ts, event_type, global_track_id, identity_id, body_score, face_score, threshold_snapshot, model_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    event_type,
                    global_track_id,
                    identity_id,
                    body_score,
                    face_score,
                    json.dumps(threshold_snapshot),
                    json.dumps(model_ids),
                ),
            )
            self._conn.commit()

    def query_face(self, _vec: np.ndarray, _k: int) -> list[BodyNeighbor]:
        return []

    def get_track_snapshot(self, global_track_id: str) -> tuple[str, Optional[str]]:
        """Return (status, identity_id or None) from persisted global_track row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, identity_id FROM global_track WHERE global_track_id = ?",
                (global_track_id,),
            ).fetchone()
        if row is None:
            return "tentative", None
        st = str(row["status"])
        rid = row["identity_id"]
        return st, (str(rid) if rid is not None else None)

    def fetch_identity_profile(self, global_track_id: str) -> tuple[Optional[str], Optional[str]]:
        row = self._conn.execute(
            """
            SELECT i.identity_id, i.display_name
            FROM global_track g
            LEFT JOIN identity i ON i.identity_id = g.identity_id
            WHERE g.global_track_id = ?
            """,
            (global_track_id,),
        ).fetchone()
        if row is None:
            return None, None
        rid = row[0]
        name = row[1]
        if rid is None:
            return None, None
        return str(rid), (str(name) if name else None)



