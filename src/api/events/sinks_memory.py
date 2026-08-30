"""In-memory outbox store and sink fakes for testing event relay and distribution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import RLock

from .ports import OutboxEvent


class InMemoryOutboxStore:
    """Thread-safe in-memory outbox store simulating FOR UPDATE SKIP LOCKED."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._events: list[OutboxEvent] = []
        self._in_flight: set[str] = set()

    def append(self, event: OutboxEvent) -> OutboxEvent:
        """Append a new outbox event."""
        with self._lock:
            seq = len(self._events) + 1
            stored = OutboxEvent(
                seq=seq,
                event_id=event.event_id,
                type=event.type,
                aggregate_id=event.aggregate_id,
                payload=event.payload,
                created_at=event.created_at,
                relayed_at=event.relayed_at,
            )
            self._events.append(stored)
            return stored

    def fetch_unrelayed(self, limit: int = 100) -> Sequence[OutboxEvent]:
        """Fetch unrelayed events skipping already in-flight rows."""
        with self._lock:
            candidates = [
                e
                for e in self._events
                if e.relayed_at is None and e.event_id not in self._in_flight
            ]
            batch = candidates[:limit]
            for e in batch:
                self._in_flight.add(e.event_id)
            return batch

    def mark_relayed(self, event_id: str, relayed_at: datetime) -> None:
        """Mark event as delivered and release in-flight lock."""
        with self._lock:
            self._in_flight.discard(event_id)
            for i, e in enumerate(self._events):
                if e.event_id == event_id:
                    self._events[i] = OutboxEvent(
                        seq=e.seq,
                        event_id=e.event_id,
                        type=e.type,
                        aggregate_id=e.aggregate_id,
                        payload=e.payload,
                        created_at=e.created_at,
                        relayed_at=relayed_at,
                    )
                    break

    def release_in_flight(self, event_id: str) -> None:
        """Release lock when delivery fails without marking relayed."""
        with self._lock:
            self._in_flight.discard(event_id)

    def get_all(self) -> Sequence[OutboxEvent]:
        with self._lock:
            return list(self._events)


class InMemorySink:
    """Thread-safe fake sink for recording deliveries and simulating transient errors."""

    def __init__(self, name: str = "memory_sink") -> None:
        self.name = name
        self._lock = RLock()
        self.received_events: list[OutboxEvent] = []
        self.delivered_ids: set[str] = set()
        self.failure_counts: dict[str, int] = {}

    def fail_times_for_event(self, event_id: str, times: int = 1) -> None:
        """Configure transient failures for a specific event."""
        with self._lock:
            self.failure_counts[event_id] = times

    def handle(self, event: OutboxEvent) -> None:
        """Receive event or fail if transient error configured."""
        with self._lock:
            remaining_failures = self.failure_counts.get(event.event_id, 0)
            if remaining_failures > 0:
                self.failure_counts[event.event_id] = remaining_failures - 1
                raise RuntimeError(
                    f"Sink '{self.name}' simulated failure for event '{event.event_id}'"
                )
            self.received_events.append(event)
            self.delivered_ids.add(event.event_id)


__all__ = [
    "InMemoryOutboxStore",
    "InMemorySink",
]
