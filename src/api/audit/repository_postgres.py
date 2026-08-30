"""PostgreSQL repository for append-only audit ledger with tail lock serialization."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .hashing import GENESIS_PREV_HASH, compute_event_hash
from .models import AuditEvent, ensure_aware_utc
from .ports import Clock, SystemClock

# `audit_events` is the agent lane's table, shared verbatim (see
# aval/contracts/fixtures/schema.sql's header): it is partitioned by
# `chain_key` so writers on different mandates never queue behind each
# other, and it carries TEXT timestamps/payload rather than
# TIMESTAMPTZ/JSONB. This repository keeps its own ledger in exactly one
# partition — a fixed chain_key, distinct from any mandate_jti or agent_id
# the agent lane uses — verified by this module's own hash algorithm
# (`compute_event_hash`), never mixed with the agent's chain math.
CHAIN_KEY = "api-ledger"


def _parse_iso(value: Any) -> datetime:
    if isinstance(value, datetime):
        return ensure_aware_utc(value)
    return ensure_aware_utc(datetime.fromisoformat(str(value)))


class PostgresLedgerRepository:
    """PostgreSQL implementation of the append-only hash-chained ledger.

    Invariants enforced:
    1. Append-only: never UPDATE/DELETE chain fields (prev_hash, hash, payload, type, mandate_id).
    2. Atomic serialization: the `chains` row for CHAIN_KEY is locked with
       `SELECT ... FOR UPDATE` before computing `prev_hash` and `hash`, so two
       concurrent appends to this ledger cannot both compute the same
       `chain_seq` — the same per-partition lock `src/agent/audit.py` takes.
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
        """Lock this ledger's chain tail, compute prev_hash & hash, and insert."""
        # Truncate to the precision the TEXT column actually stores *before*
        # hashing: hashing the untruncated value and storing the truncated
        # one would compute a hash that never matches what a later
        # recomputation (working only from what round-trips out of the
        # database) can reproduce.
        ts = ensure_aware_utc(created_at if created_at is not None else self._clock.now())
        ts = ts.astimezone(UTC).replace(microsecond=0)
        ts_text = ts.isoformat()

        with self._connect() as conn:
            with conn.cursor() as cur:
                # 1. Advisory lock, plus create-then-lock the chain row: the
                # advisory lock serializes this process's own concurrent
                # callers cheaply; the row lock is what actually stops two
                # different connections from computing the same chain_seq.
                cur.execute("SELECT pg_advisory_xact_lock(424242)")
                cur.execute(
                    "INSERT INTO chains (chain_key, head_hash, length, updated_at) "
                    "VALUES (%s, %s, 0, %s) ON CONFLICT (chain_key) DO NOTHING",
                    (CHAIN_KEY, GENESIS_PREV_HASH, ts_text),
                )
                cur.execute(
                    "SELECT head_hash, length FROM chains WHERE chain_key = %s FOR UPDATE",
                    (CHAIN_KEY,),
                )
                head = cur.fetchone()
                prev_hash = str(head["head_hash"]).strip()
                chain_seq = int(head["length"]) + 1

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
                        chain_key, chain_seq, event_id, type, mandate_jti,
                        payload, prev_hash, hash, root_sig, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s
                    ) RETURNING seq, created_at
                    """,
                    (
                        CHAIN_KEY,
                        chain_seq,
                        f"aevt_{uuid4().hex}",
                        type,
                        mandate_id,
                        json.dumps(payload),
                        prev_hash,
                        event_hash,
                        ts_text,
                    ),
                )
                row = cur.fetchone()
                seq = int(row["seq"])

                cur.execute(
                    "UPDATE chains SET head_hash = %s, length = %s, updated_at = %s "
                    "WHERE chain_key = %s",
                    (event_hash, chain_seq, ts_text, CHAIN_KEY),
                )
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
            mandate_id=str(row["mandate_jti"]),
            type=str(row["type"]),
            payload=payload,
            prev_hash=str(row["prev_hash"]).strip(),
            hash=str(row["hash"]).strip(),
            root_sig=str(row["root_sig"]) if row.get("root_sig") else None,
            created_at=_parse_iso(row["created_at"]),
        )

    def get_range(self, seq_start: int, seq_end: int) -> Sequence[AuditEvent]:
        """Fetch contiguous events within sequence range [seq_start, seq_end]."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_jti, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    WHERE chain_key = %s AND seq BETWEEN %s AND %s
                    ORDER BY seq ASC
                    """,
                    (CHAIN_KEY, seq_start, seq_end),
                )
                rows = cur.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_by_mandate(self, mandate_id: str) -> Sequence[AuditEvent]:
        """Fetch all events for a mandate ordered by seq."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_jti, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    WHERE chain_key = %s AND mandate_jti = %s
                    ORDER BY seq ASC
                    """,
                    (CHAIN_KEY, mandate_id),
                )
                rows = cur.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_all(self) -> Sequence[AuditEvent]:
        """Fetch all audit events ordered by seq."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_jti, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    WHERE chain_key = %s
                    ORDER BY seq ASC
                    """,
                    (CHAIN_KEY,),
                )
                rows = cur.fetchall()
                return [self._row_to_event(r) for r in rows]

    def get_tail(self) -> AuditEvent | None:
        """Fetch the most recent event in the ledger."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, mandate_jti, type, payload, prev_hash, hash, root_sig, created_at
                    FROM audit_events
                    WHERE chain_key = %s
                    ORDER BY seq DESC
                    LIMIT 1
                    """,
                    (CHAIN_KEY,),
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
                    WHERE chain_key = %s AND seq BETWEEN %s AND %s AND root_sig IS NULL
                    """,
                    (root_sig, CHAIN_KEY, seq_start, seq_end),
                )
                count = cur.rowcount
                conn.commit()
                return count


__all__ = ["PostgresLedgerRepository"]
