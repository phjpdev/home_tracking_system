"""Body + face fusion state machine.

Per local (camera, ByteTrack-ID) entry, holds the link to an ``identity_id``
(if any) and a deferred-conflict buffer. Body Re-ID is the continuity
backbone; face acts as evidence to **promote** a confirmed track to a
``linked`` state (named) or to flag conflicts.

States:

  tentative          -> confirmed (already handled in coordinator)
  confirmed          -> linked              (face match >= threshold_high)
  linked             -> conflict_deferred   (incoming face says someone else)
  conflict_deferred  -> linked              (majority within grace window)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class FusionState:
    state: str = "confirmed"                # confirmed | linked | conflict_deferred
    identity_id: Optional[str] = None
    last_face_score: Optional[float] = None
    pending_identity_id: Optional[str] = None
    pending_started_ts: float = 0.0
    pending_votes: Deque[str] = field(default_factory=lambda: deque(maxlen=10))
    consec_link_evidence: int = 0


class FaceFusionEngine:
    """One instance owned by ReIDCoordinator; one FusionState per local track."""

    def __init__(
        self,
        *,
        threshold_high: float,
        conflict_grace_seconds: float,
        confirmations_required: int = 2,
    ):
        self.threshold_high = float(threshold_high)
        self.conflict_grace_seconds = float(conflict_grace_seconds)
        self.confirmations_required = int(confirmations_required)
        self._by_key: dict[tuple[str, str], FusionState] = {}

    def remove(self, key: tuple[str, str]) -> None:
        self._by_key.pop(key, None)

    def get_or_create(self, key: tuple[str, str]) -> FusionState:
        st = self._by_key.get(key)
        if st is None:
            st = FusionState()
            self._by_key[key] = st
        return st

    def state_snapshot(self, key: tuple[str, str]) -> Optional[FusionState]:
        return self._by_key.get(key)

    def process(
        self,
        key: tuple[str, str],
        *,
        face_match_identity_id: Optional[str],
        face_match_distance: Optional[float],
        body_state: str,
        ts: float,
    ) -> tuple[str, Optional[str], list[dict]]:
        """Advance the FSM and return (new_state, identity_id, fusion_events).

        Each item in ``fusion_events`` is a dict suitable for the
        ``GallerySqliteFaiss.log_fusion_event`` schema (without ts).
        """
        events: list[dict] = []
        st = self.get_or_create(key)
        if body_state == "tentative":
            # Don't fuse until body is stable.
            return st.state, st.identity_id, events

        if face_match_identity_id is None or face_match_distance is None:
            self._maybe_resolve_pending(st, ts, events)
            return st.state, st.identity_id, events

        in_band = face_match_distance <= self.threshold_high
        st.last_face_score = float(face_match_distance)

        if not in_band:
            self._maybe_resolve_pending(st, ts, events)
            return st.state, st.identity_id, events

        if st.state == "confirmed" and st.identity_id is None:
            st.consec_link_evidence += 1
            if st.consec_link_evidence >= self.confirmations_required:
                st.identity_id = face_match_identity_id
                st.state = "linked"
                events.append({
                    "event_type": "link",
                    "global_track_id": None,
                    "identity_id": face_match_identity_id,
                    "body_score": None,
                    "face_score": float(face_match_distance),
                })
            return st.state, st.identity_id, events

        if st.state == "linked":
            if face_match_identity_id == st.identity_id:
                st.consec_link_evidence += 1
                self._reset_pending(st)
                return st.state, st.identity_id, events
            # Contradictory evidence.
            if st.pending_identity_id is None:
                st.pending_identity_id = face_match_identity_id
                st.pending_started_ts = ts
                st.pending_votes.clear()
            st.pending_votes.append(face_match_identity_id)
            events.append({
                "event_type": "conflict_deferred",
                "global_track_id": None,
                "identity_id": st.identity_id,
                "body_score": None,
                "face_score": float(face_match_distance),
                "_pending": face_match_identity_id,
            })
            st.state = "conflict_deferred"
            return st.state, st.identity_id, events

        if st.state == "conflict_deferred":
            st.pending_votes.append(face_match_identity_id)
            if face_match_identity_id == st.identity_id:
                st.consec_link_evidence += 1
            self._maybe_resolve_pending(st, ts, events, force=False)
            return st.state, st.identity_id, events

        return st.state, st.identity_id, events

    def _reset_pending(self, st: FusionState) -> None:
        st.pending_identity_id = None
        st.pending_started_ts = 0.0
        st.pending_votes.clear()

    def _maybe_resolve_pending(
        self,
        st: FusionState,
        ts: float,
        events: list[dict],
        force: bool = False,
    ) -> None:
        if st.pending_identity_id is None or st.pending_started_ts == 0.0:
            return
        elapsed = ts - st.pending_started_ts
        if not force and elapsed < self.conflict_grace_seconds:
            return
        # Majority vote.
        counts: dict[str, int] = {}
        for v in st.pending_votes:
            counts[v] = counts.get(v, 0) + 1
        # Add current identity as one implicit vote so a tie keeps it.
        if st.identity_id is not None:
            counts[st.identity_id] = counts.get(st.identity_id, 0) + 1
        winner = max(counts.items(), key=lambda kv: kv[1])[0]

        prior = st.identity_id
        if winner == prior:
            events.append({
                "event_type": "conflict_resolved_keep",
                "global_track_id": None,
                "identity_id": prior,
                "body_score": None,
                "face_score": st.last_face_score,
            })
        else:
            events.append({
                "event_type": "relink",
                "global_track_id": None,
                "identity_id": winner,
                "body_score": None,
                "face_score": st.last_face_score,
            })
            st.identity_id = winner

        st.state = "linked"
        self._reset_pending(st)
