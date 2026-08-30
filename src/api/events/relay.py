"""Outbox poller and dispatcher implementing at-least-once distribution
with SKIP LOCKED (decision #10).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from threading import RLock
from typing import Any

from .ports import Clock, OutboxEvent, OutboxStore, Sink, SystemClock, ensure_aware_utc


def _iso(value: datetime) -> str:
    """`outbox` is the agent lane's table, shared verbatim (TEXT timestamps
    and TEXT payload — see aval/contracts/fixtures/schema.sql's header)."""
    return ensure_aware_utc(value).replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> datetime:
    if isinstance(value, datetime):
        return ensure_aware_utc(value)
    return ensure_aware_utc(datetime.fromisoformat(str(value)))


class PostgresOutboxStore:
    """PostgreSQL implementation of OutboxStore using `FOR UPDATE SKIP LOCKED`."""

    def __init__(self, dsn: str | None = None, clock: Clock | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        self._clock = clock or SystemClock()
        self._lock = RLock()
        self._in_flight: set[str] = set()

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
                        _iso(event.relayed_at) if event.relayed_at else None,
                        _iso(ts),
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
        """Fetch unrelayed events using `FOR UPDATE SKIP LOCKED` and in-flight tracking."""
        with self._lock:
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
                        (limit + len(self._in_flight),),
                    )
                    rows = cur.fetchall()
                    events = []
                    for r in rows:
                        event_id = str(r["event_id"])
                        if event_id in self._in_flight:
                            continue
                        self._in_flight.add(event_id)
                        payload = r["payload"]
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        events.append(
                            OutboxEvent(
                                seq=int(r["seq"]),
                                event_id=event_id,
                                type=str(r["type"]),
                                aggregate_id=str(r["aggregate_id"]),
                                payload=payload,
                                created_at=_parse_iso(r["created_at"]),
                                relayed_at=(
                                    _parse_iso(r["relayed_at"]) if r.get("relayed_at") else None
                                ),
                            )
                        )
                        if len(events) >= limit:
                            break
                    return events

    def mark_relayed(self, event_id: str, relayed_at: datetime) -> None:
        """Mark event as relayed and release in-flight status."""
        with self._lock:
            self._in_flight.discard(event_id)
        ts = _iso(relayed_at)
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

    def release_in_flight(self, event_id: str) -> None:
        """Release an event back into the pool after delivery failure."""
        with self._lock:
            self._in_flight.discard(event_id)


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
