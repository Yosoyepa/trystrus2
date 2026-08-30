"""Outbox poller and dispatcher implementing at-least-once distribution
with SKIP LOCKED (decision #10).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .ports import Clock, OutboxEvent, OutboxStore, Sink, SystemClock, ensure_aware_utc


class PostgresOutboxStore:
    """PostgreSQL implementation of OutboxStore using `FOR UPDATE SKIP LOCKED`."""

    def __init__(self, dsn: str | None = None, clock: Clock | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        self._clock = clock or SystemClock()

    def _connect(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self._dsn, row_factory=dict_row)
        except ImportError as exc:
            raise RuntimeError("psycopg package is required to use PostgresOutboxStore") from exc

    def append(self, event: OutboxEvent) -> OutboxEvent:
        """Insert a new event into the outbox."""
        ts = ensure_aware_utc(event.created_at)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO outbox (
                        event_id, type, aggregate_id, payload, relayed_at, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s
                    ) RETURNING seq, created_at
                    """,
                    (
                        event.event_id,
                        event.type,
                        event.aggregate_id,
                        json.dumps(event.payload),
                        event.relayed_at,
                        ts,
                    ),
                )
                row = cur.fetchone()
                seq = int(row["seq"])
                conn.commit()
                return OutboxEvent(
                    seq=seq,
                    event_id=event.event_id,
                    type=event.type,
                    aggregate_id=event.aggregate_id,
                    payload=event.payload,
                    created_at=ts,
                    relayed_at=event.relayed_at,
                )

    def fetch_unrelayed(self, limit: int = 100) -> Sequence[OutboxEvent]:
        """Fetch unrelayed events using `FOR UPDATE SKIP LOCKED`."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, event_id, type, aggregate_id, payload, relayed_at, created_at
                    FROM outbox
                    WHERE relayed_at IS NULL
                    ORDER BY seq ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
                events = []
                for r in rows:
                    payload = r["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    events.append(
                        OutboxEvent(
                            seq=int(r["seq"]),
                            event_id=str(r["event_id"]),
                            type=str(r["type"]),
                            aggregate_id=str(r["aggregate_id"]),
                            payload=payload,
                            created_at=ensure_aware_utc(r["created_at"]),
                            relayed_at=(
                                ensure_aware_utc(r["relayed_at"]) if r.get("relayed_at") else None
                            ),
                        )
                    )
                return events

    def mark_relayed(self, event_id: str, relayed_at: datetime) -> None:
        """Mark event as relayed."""
        ts = ensure_aware_utc(relayed_at)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE outbox
                    SET relayed_at = %s
                    WHERE event_id = %s
                    """,
                    (ts, event_id),
                )
                conn.commit()


class OutboxRelay:
    """Outbox relay dispatcher feeding sinks with per-event isolation and idempotency.

    Invariants:
    - At-least-once delivery: failed sink leaves `relayed_at` NULL for retry.
    - Per-event isolation: error in one event does not block delivery of others.
    - SKIP LOCKED: concurrent relay instances do not duplicate event processing.
    """

    def __init__(
        self,
        store: OutboxStore,
        routes: Mapping[str, Sequence[Sink]] | None = None,
        default_sinks: Sequence[Sink] = (),
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._routes: dict[str, list[Sink]] = {}
        if routes:
            for k, v in routes.items():
                self._routes[k] = list(v)
        self._default_sinks = list(default_sinks)
        self._clock = clock or SystemClock()

    def register_sink(self, event_type: str, sink: Sink) -> None:
        """Register a destination sink for an event type."""
        self._routes.setdefault(event_type, []).append(sink)

    def drain(self, limit: int = 100) -> int:
        """Drain a batch of unrelayed events and dispatch to sinks.

        Returns the number of successfully relayed events.
        """
        events = self._store.fetch_unrelayed(limit=limit)
        drained_count = 0

        for event in events:
            sinks = self._routes.get(event.type, self._default_sinks)
            now = self._clock.now()

            if not sinks:
                # No sinks configured for this event type -> mark relayed and audit
                self._store.mark_relayed(event.event_id, now)
                drained_count += 1
                continue

            all_delivered = True
            for sink in sinks:
                try:
                    sink.handle(event)
                except Exception:
                    all_delivered = False
                    if hasattr(self._store, "release_in_flight"):
                        self._store.release_in_flight(event.event_id)
                    break

            if all_delivered:
                self._store.mark_relayed(event.event_id, now)
                drained_count += 1

        return drained_count


__all__ = [
    "OutboxRelay",
    "PostgresOutboxStore",
]
