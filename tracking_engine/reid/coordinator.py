"""Per-tick orchestration between ByteTrack IDs, embeddings, SQLite gallery, and FAISS."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .config import ReidConfig
from .crop_quality import assess_crop_quality, clipped_head_bbox, person_crop
from .embed import BodyEmbedder, OnnxBodyEmbedder, create_body_embedder
from .face_embed import FaceStack, maybe_create_face_stack
from .fusion import FaceFusionEngine
from .gallery_sqlite import BodyNeighbor, GallerySqliteFaiss


@dataclass
class _LocalAssoc:
    global_id: str
    ema: Optional[np.ndarray]
    local_track_state: str
    consec_match: int
    last_seen: float


class ReIDCoordinator:
    """Maps (camera, ByteTrack label) → global_track_id; updates gallery learning rules (Phase A)."""

    def __init__(self, cfg: ReidConfig) -> None:
        if cfg.backend != "sqlite_faiss":
            raise RuntimeError(
                f'reid.backend must be "sqlite_faiss" for this build (got {cfg.backend!r})'
            )
        self.cfg = cfg
        self.gallery = GallerySqliteFaiss(cfg)
        self._embedder: BodyEmbedder = create_body_embedder(cfg)
        self._face: Optional[FaceStack] = maybe_create_face_stack(
            enabled=cfg.face_enabled,
            detector_onnx_path=str(cfg.face_detector_onnx_path) if cfg.face_detector_onnx_path else None,
            embedder_onnx_path=str(cfg.face_embedder_onnx_path) if cfg.face_embedder_onnx_path else None,
            dimension=cfg.face_dimension,
            min_face_size=cfg.face_min_face_size,
            min_frontal_score=cfg.face_min_frontal_score,
        )
        self._fusion = FaceFusionEngine(
            threshold_high=cfg.face_threshold_high,
            conflict_grace_seconds=cfg.conflict_grace_seconds,
        )
        self._map: dict[tuple[str, str], _LocalAssoc] = {}
        self._alive_this_tick: set[tuple[str, str]] = set()
        self._writes: deque[float] = deque()

    def close(self) -> None:
        self.gallery.close()

    def embedder_backend(self) -> str:
        return "onnx" if isinstance(self._embedder, OnnxBodyEmbedder) else "fallback_opencv"

    def begin_tick(self) -> None:
        self._alive_this_tick.clear()

    def prune_stale(self, ts: float) -> None:
        ttl = float(self.cfg.mapping_ttl_sec)
        dead = [
            key
            for key, assoc in self._map.items()
            if key not in self._alive_this_tick and ts - assoc.last_seen > ttl
        ]
        for key in dead:
            del self._map[key]
            self._fusion.remove(key)

    def face_enabled(self) -> bool:
        return self._face is not None

    def observe(
        self,
        *,
        cam_id: str,
        frame_ts: float,
        frame_bgr: np.ndarray,
        xyxy: tuple[float, float, float, float],
        tracker_id: int,
    ) -> dict[str, Any]:
        label = f"t{int(tracker_id)}"
        key = (cam_id, label)
        self._alive_this_tick.add(key)

        assoc_before = self._map.get(key)
        prev_ts = assoc_before.last_seen if assoc_before is not None else frame_ts
        dt = float(frame_ts - prev_ts)

        crop = person_crop(frame_bgr, xyxy)
        if crop is None:
            if assoc_before is None:
                return {}
            qa_fake = assess_crop_quality(
                np.zeros((1, 1, 3), dtype=np.uint8),
                min_bbox_area=float(self.cfg.min_bbox_area),
                min_blur_var=float(self.cfg.min_blur_var),
                min_aspect=float(self.cfg.min_aspect),
                max_aspect=float(self.cfg.max_aspect),
            )
            assoc_before.last_seen = float(frame_ts)
            self._map[key] = assoc_before
            nearest_out: Optional[float] = None
            if assoc_before.ema is not None:
                nn_probe = self.gallery.query_body(assoc_before.ema, 1)
                nearest_out = float(nn_probe[0].distance) if nn_probe else None
            return self._extras_for_assoc(
                assoc=assoc_before,
                label=label,
                nearest_distance=nearest_out,
                omit_local_duplicate=True,
            )

        qa = assess_crop_quality(
            crop,
            min_bbox_area=float(self.cfg.min_bbox_area),
            min_blur_var=float(self.cfg.min_blur_var),
            min_aspect=float(self.cfg.min_aspect),
            max_aspect=float(self.cfg.max_aspect),
        )
        qa_ok = qa.ok

        emb: Optional[np.ndarray] = None
        if qa_ok:
            emb = self._embedder.embed_batch([crop])[0]

        nearest_dist: Optional[float] = None
        assoc: Optional[_LocalAssoc] = assoc_before

        if assoc is None:
            if emb is None:
                return {}

            neighbors = self.gallery.query_body(emb, self.cfg.top_k)
            nearest_dist = float(neighbors[0].distance) if neighbors else None

            if neighbors and neighbors[0].distance <= float(self.cfg.threshold_match):
                gid = str(neighbors[0].global_track_id)
                self.gallery.touch_global_track(gid, frame_ts, status=None)
            else:
                gid = self.gallery.create_global_track(
                    tenant_id=self.cfg.tenant_id, status="tentative", ts=frame_ts
                )

            assoc = _LocalAssoc(
                global_id=gid,
                ema=emb.copy(),
                local_track_state="tentative",
                consec_match=1,
                last_seen=float(frame_ts),
            )

            self._map[key] = assoc
            self.gallery.touch_global_track(gid, frame_ts, status=None)
        else:
            gid_old = assoc.global_id

            new_ema: Optional[np.ndarray]
            if emb is None:
                new_ema = assoc.ema
            elif assoc.ema is None:
                new_ema = emb.copy()
            elif dt > float(self.cfg.ema_reset_gap_sec):
                new_ema = emb.copy()
            else:
                a = float(self.cfg.ema_alpha)
                new_ema = (a * emb + (1.0 - a) * assoc.ema).astype(np.float32)

            assoc.ema = new_ema

            consec = assoc.consec_match
            neighbors: list[BodyNeighbor] = []
            if assoc.ema is not None:
                neighbors = self.gallery.query_body(assoc.ema, self.cfg.top_k)
                nearest_dist = float(neighbors[0].distance) if neighbors else None
                aligned = False
                if neighbors and neighbors[0].distance <= float(self.cfg.threshold_match):
                    gid_top = str(neighbors[0].global_track_id)
                    aligned = gid_top == str(gid_old)
                elif not neighbors:
                    aligned = True
                consec = consec + 1 if aligned else max(0, consec - 1)

            assoc.consec_match = consec
            assoc.last_seen = float(frame_ts)
            self.gallery.touch_global_track(str(gid_old), frame_ts, status=None)

            if (
                assoc.local_track_state == "tentative"
                and assoc.consec_match
                >= int(self.cfg.tentative_to_confirmed_frames)
            ):
                assoc.local_track_state = "confirmed"
                self.gallery.touch_global_track(str(gid_old), frame_ts, status="confirmed")

            self._maybe_learn(
                qa_ok=qa_ok,
                quality_score=float(qa.quality_score),
                assoc=assoc,
                cam_id=cam_id,
                label=label,
                frame_ts=frame_ts,
                emb=emb,
            )

            self._map[key] = assoc

        assert assoc is not None

        nearest_dist_out = nearest_dist
        if nearest_dist_out is None and assoc.ema is not None:
            nn_probe = self.gallery.query_body(assoc.ema, 1)
            nearest_dist_out = float(nn_probe[0].distance) if nn_probe else None

        face_score = self._maybe_fuse_face(
            cam_id=cam_id,
            label=label,
            frame_bgr=frame_bgr,
            xyxy=xyxy,
            frame_ts=frame_ts,
            assoc=assoc,
        )

        extras = self._extras_for_assoc(
            assoc=assoc,
            label=label,
            nearest_distance=nearest_dist_out,
        )
        if face_score is not None:
            extras["face_score"] = round(float(face_score), 4)
        return extras

    def _extras_for_assoc(
        self,
        *,
        assoc: _LocalAssoc,
        label: str,
        nearest_distance: Optional[float],
        omit_local_duplicate: bool = False,
    ) -> dict[str, Any]:
        db_status, _ = self.gallery.get_track_snapshot(str(assoc.global_id))
        display_state = str(db_status)
        if assoc.local_track_state == "confirmed" and display_state == "tentative":
            display_state = "confirmed"

        out: dict[str, Any] = {
            "global_id": str(assoc.global_id),
            "track_state": display_state,
        }

        pid, pname = self.gallery.fetch_identity_profile(str(assoc.global_id))
        if pid is not None:
            out["identity_id"] = pid
            if pname:
                out["identity_name"] = pname
                out["track_state"] = "linked"

        if nearest_distance is not None:
            out["reid_score"] = round(float(nearest_distance), 4)

        if label and not omit_local_duplicate:
            out["local_id"] = label

        return out

    def _maybe_fuse_face(
        self,
        *,
        cam_id: str,
        label: str,
        frame_bgr: np.ndarray,
        xyxy: tuple[float, float, float, float],
        frame_ts: float,
        assoc: _LocalAssoc,
    ) -> Optional[float]:
        if self._face is None:
            return None
        if assoc.local_track_state != "confirmed":
            return None

        fh, fw = frame_bgr.shape[:2]
        hx1, hy1, hx2, hy2 = clipped_head_bbox(xyxy, fh, fw)
        if hx2 <= hx1 or hy2 <= hy1:
            return None
        head_crop = frame_bgr[hy1:hy2, hx1:hx2]
        if head_crop.size == 0:
            return None

        face_res = self._face.detect_and_embed_head(head_crop)
        face_match_pid: Optional[str] = None
        face_match_dist: Optional[float] = None
        if face_res is not None:
            neighbors = self.gallery.query_face(face_res.embedding, k=3)
            if neighbors:
                face_match_pid = neighbors[0].identity_id
                face_match_dist = float(neighbors[0].distance)

        key = (cam_id, label)
        new_state, identity_id, events = self._fusion.process(
            key,
            face_match_identity_id=face_match_pid,
            face_match_distance=face_match_dist,
            body_state=assoc.local_track_state,
            ts=float(frame_ts),
        )

        if identity_id is not None:
            db_pid, _ = self.gallery.get_track_snapshot(str(assoc.global_id))
            if db_pid != "linked":
                self.gallery.link_track_to_identity(
                    str(assoc.global_id),
                    identity_id,
                    tenant_id=self.cfg.tenant_id,
                    scores={"face": face_match_dist or 0.0},
                )

        for ev in events:
            self.gallery.log_fusion_event(
                event_type=ev["event_type"],
                global_track_id=str(assoc.global_id),
                identity_id=ev.get("identity_id"),
                body_score=ev.get("body_score"),
                face_score=ev.get("face_score"),
                threshold_snapshot={
                    "face_threshold_high": self.cfg.face_threshold_high,
                    "body_threshold_match": self.cfg.threshold_match,
                },
                model_ids={
                    "body": self.cfg.body_model_id,
                    "face": self.cfg.face_model_id,
                },
            )

        return face_match_dist

    def _writes_allowed(self, ts: float) -> bool:
        window_sec = 60.0
        while self._writes and float(ts - self._writes[0]) > window_sec:
            self._writes.popleft()
        return len(self._writes) < int(self.cfg.max_writes_per_minute)

    def _maybe_learn(
        self,
        *,
        qa_ok: bool,
        quality_score: float,
        assoc: _LocalAssoc,
        cam_id: str,
        label: str,
        frame_ts: float,
        emb: Optional[np.ndarray],
    ) -> None:
        if assoc.local_track_state != "confirmed":
            return
        if not qa_ok or emb is None:
            return
        if assoc.ema is None:
            return
        if quality_score < float(self.cfg.min_quality_to_store):
            return

        neighbors = self.gallery.query_body(assoc.ema, max(8, self.cfg.top_k))
        if not neighbors:
            return

        aligned = neighbors[0]
        if aligned.global_track_id is None:
            return
        if str(aligned.global_track_id) != str(assoc.global_id):
            return

        # Confident reaffirmation: close to nearest neighbor of ourselves.
        if float(aligned.distance) > float(self.cfg.threshold_match):
            return

        prototypes = self.gallery.count_prototypes_global(str(assoc.global_id))
        if prototypes >= int(self.cfg.max_prototypes_per_identity):
            return

        d_dup = self.gallery.min_distance_to_own_prototypes(str(assoc.global_id), emb)
        if d_dup is not None and float(d_dup) < float(self.cfg.dedup_distance):
            return

        if not self._writes_allowed(frame_ts):
            return

        self.gallery.insert_appearance(
            global_track_id=str(assoc.global_id),
            embedding=np.asarray(emb, dtype=np.float32),
            quality=float(quality_score),
            cam_id=cam_id,
            local_track_label=label,
            frame_ts=float(frame_ts),
        )
        self._writes.append(float(frame_ts))
