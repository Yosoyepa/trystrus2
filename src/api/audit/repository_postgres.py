"""PostgreSQL repository for append-only audit ledger with tail lock serialization."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from .hashing import GENESIS_PREV_HASH, compute_event_hash
from .models import AuditEvent, ensure_aware_utc
from .ports import Clock, SystemClock


class PostgresLedgerRepository:
    """PostgreSQL implementation of the append-only hash-chained ledger.

    Invariants enforced:
    1. Append-only: never UPDATE/DELETE chain fields (prev_hash, hash, payload, type, mandate_id).
    2. Atomic serialization: tail is locked via `SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE`
       before computing `prev_hash` and `hash` to prevent chain branching.
    3. Deterministic hashing: hash uses canonical serialization with app-supplied UTC timestamp.
    4. Guarded checkpoint annotation: `UPDATE ... WHERE root_sig IS NULL` touching ONLY root_sig.
    """

    def __init__(
        self,
        dsn: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        self._clock = clock or SystemClock()

    def _connect(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self._dsn, row_factory=dict_row)
        except ImportError as exc:
            raise RuntimeError(
                "psycopg package is required to use PostgresLedgerRepository"
            ) from exc

    def append(
        self,
        *,
        mandate_id: str,
        type: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        """Atomically lock tail, compute prev_hash & hash, and insert audit event."""
        ts = ensure_aware_utc(created_at if created_at is not None else self._clock.now())

        with self._connect() as conn:
            with conn.cursor() as cur:
                # 1. Acquire transaction advisory lock to serialize appends
                cur.execute("SELECT pg_advisory_xact_lock(424242)")
                cur.execute(
                    "SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1 FOR UPDATE"
                )
                last_row = cur.fetchone()

                if last_row is None:
                    prev_hash = GENESIS_PREV_HASH
                else:
                    prev_hash = str(last_row["hash"]).strip()

                # 2. Compute canonical deterministic hash
                event_hash = compute_event_hash(
                    mandate_id=mandate_id,
                    type=type,
                    payload=payload,
                    prev_hash=prev_hash,
                    created_at=ts,
                )

                # 3. Insert new row
                cur.execute(
                    """
                    INSERT INTO audit_events (
                        mandate_id, type, payload, prev_hash, hash, root_sig, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, NULL, %s
                    ) RETURNING seq, created_at
                    """,
                    (
                        mandate_id,
                        type,
                        json.dumps(payload),
                        prev_hash,
                        event_hash,
                        ts,
                    ),
                )
                row = cur.fetchone()
                seq = int(row["seq"])
                conn.commit()

                return AuditEvent(
                    seq=seq,
                    mandate_id=mandate_id,
                    type=type,
                    payload=payload,
                    prev_hash=prev_hash,
                    hash=event_hash,
                    root_sig=None,
                    created_at=ts,
                )

    def _row_to_event(self, row: dict[str, Any]) -> AuditEvent:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return AuditEvent(
            seq=int(row["seq"]),
            mandate_id=str(row["mandate_id"]),
            type=str(row["type"]),
            payload=payload,
            prev_hash=str(row["prev_hash"]).strip(),
            hash=str(row["hash"]).strip(),
            root_sig=str(row["root_sig"]) if row.get("root_sig") else None,
            created_at=ensure_aware_utc(row["created_at"]),
        )

    def get_range(self, seq_start: int, seq_end: int) -> Sequence[AuditEvent]:
        """Fetch contiguous events within sequence range [seq_start, seq_end]."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_id, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    WHERE seq BETWEEN %s AND %s
                    ORDER BY seq ASC
                    """,
                    (seq_start, seq_end),
                )
                rows = cur.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_by_mandate(self, mandate_id: str) -> Sequence[AuditEvent]:
        """Fetch all events for a mandate ordered by seq."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_id, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    WHERE mandate_id = %s
                    ORDER BY seq ASC
                    """,
                    (mandate_id,),
                )
                rows = cur.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_all(self) -> Sequence[AuditEvent]:
        """Fetch all audit events ordered by seq."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_id, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    ORDER BY seq ASC
                    """
                )
                rows = cur.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_tail(self) -> AuditEvent | None:
        """Fetch the most recent event in the ledger."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_id, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    ORDER BY seq DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                return self._row_to_event(row) if row else None

    def annotate_root(self, seq_start: int, seq_end: int, root_sig: str) -> int:
        """Guarded update: record root_sig WHERE root_sig IS NULL without touching chain."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE audit_events
                    SET root_sig = %s
                    WHERE seq BETWEEN %s AND %s AND root_sig IS NULL
                    """,
                    (root_sig, seq_start, seq_end),
                )
                count = cur.rowcount
                conn.commit()
                return count


__all__ = ["PostgresLedgerRepository"]
