"""In-memory fake repository for audit hash chain with tamper test hooks."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import RLock
from typing import Any

from .hashing import GENESIS_PREV_HASH, compute_event_hash
from .models import AuditEvent
from .ports import Clock, SystemClock


class InMemoryLedgerRepository:
    """Thread-safe in-memory ledger repository with tamper injection for security tests."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._lock = RLock()
        self._events: list[AuditEvent] = []
        self._clock = clock or SystemClock()

    def append(
        self,
        *,
        mandate_id: str,
        type: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        """Atomically compute hash with tail-lock and append a new event."""
        with self._lock:
            seq = len(self._events) + 1
            prev_hash = self._events[-1].hash if self._events else GENESIS_PREV_HASH
            ts = created_at if created_at is not None else self._clock.now()
            event_hash = compute_event_hash(
                mandate_id=mandate_id,
                type=type,
                payload=payload,
                prev_hash=prev_hash,
                created_at=ts,
            )
            event = AuditEvent(
                seq=seq,
                mandate_id=mandate_id,
                type=type,
                payload=payload,
                prev_hash=prev_hash,
                hash=event_hash,
                root_sig=None,
                created_at=ts,
            )
            self._events.append(event)
            return event

    def get_range(self, seq_start: int, seq_end: int) -> Sequence[AuditEvent]:
        """Fetch events in sequence range [seq_start, seq_end]."""
        with self._lock:
            return [
                e
                for e in self._events
                if e.seq is not None and seq_start <= e.seq <= seq_end
            ]

    def get_by_mandate(self, mandate_id: str) -> Sequence[AuditEvent]:
        """Fetch all events for a mandate."""
        with self._lock:
            return [e for e in self._events if e.mandate_id == mandate_id]

    def get_all(self) -> Sequence[AuditEvent]:
        """Fetch all audit events in ascending seq order."""
        with self._lock:
            return list(self._events)

    def get_tail(self) -> AuditEvent | None:
        """Fetch latest audit event."""
        with self._lock:
            return self._events[-1] if self._events else None

    def annotate_root(self, seq_start: int, seq_end: int, root_sig: str) -> int:
        """Guarded update of root_sig on events in range WHERE root_sig IS NULL."""
        with self._lock:
            updated = 0
            new_events: list[AuditEvent] = []
            for e in self._events:
                if e.seq is not None and seq_start <= e.seq <= seq_end and e.root_sig is None:
                    annotated = AuditEvent(
                        seq=e.seq,
                        mandate_id=e.mandate_id,
                        type=e.type,
                        payload=e.payload,
                        prev_hash=e.prev_hash,
                        hash=e.hash,
                        root_sig=root_sig,
                        created_at=e.created_at,
                    )
                    new_events.append(annotated)
                    updated += 1
                else:
                    new_events.append(e)
            self._events = new_events
            return updated

    def tamper(self, seq: int, field_name: str, value: Any) -> None:
        """Testing hook to tamper with any stored event field to verify detection."""
        with self._lock:
            target_idx = None
            for idx, e in enumerate(self._events):
                if e.seq == seq:
                    target_idx = idx
                    break
            if target_idx is None:
                raise ValueError(f"Event with seq={seq} not found")

            target = self._events[target_idx]
            d = target.to_dict()
            d[field_name] = value

            try:
                tampered = AuditEvent(
                    seq=d["seq"],
                    mandate_id=d["mandate_id"],
                    type=d["type"],
                    payload=d["payload"],
                    prev_hash=d["prev_hash"],
                    hash=d["hash"],
                    root_sig=d["root_sig"],
                    created_at=d["created_at"],
                )
            except Exception:
                # Bypass validation for test scenarios (e.g. invalid hash lengths)
                tampered = object.__new__(AuditEvent)
                for k, v in d.items():
                    object.__setattr__(tampered, k, v)

            self._events[target_idx] = tampered


__all__ = ["InMemoryLedgerRepository"]
