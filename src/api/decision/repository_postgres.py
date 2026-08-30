"""PostgreSQL adapters for DEV2 velocity, idempotency, and reservation ports."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import uuid4

from src.api.domain.idempotency import (
    IDEMPOTENCY_TTL,
    IdempotencyConflict,
    IdempotencyRecord,
    derive_idempotency_key,
    make_record,
)
from src.api.domain.models import MandateStatus, SpendView, amount_decimal

from .ports import OutboxEvent

ConnectionFactory = Callable[[], Any]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _money(value: Decimal | str | int) -> Decimal:
    return amount_decimal(value)


def _minute(value: datetime) -> datetime:
    return _utc(value).replace(second=0, microsecond=0)


def _iso(value: datetime) -> str:
    """Format for the columns this module writes that are TEXT, not
    TIMESTAMPTZ — `mandates`, `purchases` and `outbox` are the agent lane's
    tables, shared verbatim (aval/contracts/fixtures/schema.sql)."""
    return _utc(value).replace(microsecond=0).isoformat()


class _PostgresAdapter:
    def __init__(
        self, connection: Any = None, connection_factory: ConnectionFactory | None = None
    ) -> None:
        if connection is None and connection_factory is None:
            raise ValueError("a PostgreSQL connection or connection_factory is required")
        self._connection = connection
        self._connection_factory = connection_factory

    @contextmanager
    def _transaction(self, transaction: Any = None) -> Iterator[Any]:
        if transaction is not None:
            yield transaction
            return
        owned = self._connection is None
        connection = self._connection_factory() if owned else self._connection
        try:
            yield connection
        except Exception:
            rollback = getattr(connection, "rollback", None)
            if rollback is not None:
                rollback()
            raise
        else:
            commit = getattr(connection, "commit", None)
            if commit is not None:
                commit()
        finally:
            if owned:
                close = getattr(connection, "close", None)
                if close is not None:
                    close()

    @staticmethod
    @contextmanager
    def _cursor(connection: Any) -> Iterator[Any]:
        cursor = connection.cursor()
        try:
            yield cursor
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()


class PostgresVelocityStore(_PostgresAdapter):
    """Atomic counter adapter backed by ``velocity_counters``."""

    _UPSERT = """
        INSERT INTO velocity_counters
            (mandate_id, counter, "window", bucket_start, val)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (mandate_id, counter, "window", bucket_start)
        DO UPDATE SET val = velocity_counters.val + EXCLUDED.val
    """

    _COUNT = """
        SELECT COALESCE(SUM(val), 0)
        FROM velocity_counters
        WHERE mandate_id = %s
          AND counter = %s
          AND "window" = %s
          AND bucket_start >= %s
          AND bucket_start <= %s
    """

    def _add(
        self,
        mandate_id: str,
        counter: str,
        window: str,
        bucket_start: datetime,
        value: Decimal | int,
        *,
        transaction: Any = None,
    ) -> None:
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(self._UPSERT, (mandate_id, counter, window, bucket_start, value))

    def increment_intent(
        self,
        mandate_id: str,
        amount: Decimal | str,
        now: datetime,
        *,
        transaction: Any = None,
    ) -> None:
        bucket = _minute(now)
        amount_value = _money(amount)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(self._UPSERT, (mandate_id, "intents", "1m", bucket, 1))
            cursor.execute(
                self._UPSERT,
                (mandate_id, "amount_sum", "1m", bucket, amount_value),
            )

    record_intent = increment_intent

    def increment_escalation(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        self._add(mandate_id, "escalations", "1h", _minute(now), 1, transaction=transaction)

    record_escalation = increment_escalation

    def increment_open_authorizations(
        self,
        mandate_id: str,
        now: datetime,
        *,
        transaction: Any = None,
    ) -> None:
        _utc(now)
        self._add(
            mandate_id,
            "open_authz",
            "current",
            datetime(1970, 1, 1, tzinfo=UTC),
            1,
            transaction=transaction,
        )

    open_authorization = increment_open_authorizations

    def decrement_open_authorizations(
        self,
        mandate_id: str,
        now: datetime,
        *,
        transaction: Any = None,
    ) -> None:
        _utc(now)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(
                """
                INSERT INTO velocity_counters
                    (mandate_id, counter, "window", bucket_start, val)
                VALUES (%s, 'open_authz', 'current', %s, 0)
                ON CONFLICT (mandate_id, counter, "window", bucket_start)
                DO UPDATE SET val = GREATEST(0, velocity_counters.val - 1)
                """,
                (mandate_id, datetime(1970, 1, 1, tzinfo=UTC)),
            )

    release_authorization = decrement_open_authorizations

    def count_intents(
        self,
        mandate_id: str,
        now: datetime,
        window: timedelta = timedelta(seconds=60),
        *,
        transaction: Any = None,
    ) -> int:
        current = _utc(now)
        start = _minute(current - window)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(self._COUNT, (mandate_id, "intents", "1m", start, _minute(current)))
            row = cursor.fetchone()
        return int(row[0] if row and row[0] is not None else 0)

    def count_escalations(self, mandate_id: str, now: datetime, *, transaction: Any = None) -> int:
        current = _utc(now)
        start = _minute(current - timedelta(hours=1))
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(self._COUNT, (mandate_id, "escalations", "1h", start, _minute(current)))
            row = cursor.fetchone()
        return int(row[0] if row and row[0] is not None else 0)

    def amount_sum(self, mandate_id: str, now: datetime, *, transaction: Any = None) -> Decimal:
        current = _utc(now)
        start = _minute(current - timedelta(seconds=60))
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(self._COUNT, (mandate_id, "amount_sum", "1m", start, _minute(current)))
            row = cursor.fetchone()
        return _money(row[0] if row and row[0] is not None else 0)

    def open_authorizations(
        self, mandate_id: str, now: datetime | None = None, *, transaction: Any = None
    ) -> int:
        if now is not None:
            _utc(now)
        bucket = datetime(1970, 1, 1, tzinfo=UTC)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(
                """
                SELECT COALESCE(val, 0)
                FROM velocity_counters
                WHERE mandate_id = %s AND counter = 'open_authz'
                  AND "window" = 'current' AND bucket_start = %s
                """,
                (mandate_id, bucket),
            )
            row = cursor.fetchone()
        return max(0, int(row[0] if row and row[0] is not None else 0))

    def get_cooldown(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> datetime | None:
        current = _utc(now)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(
                """
                SELECT MAX(bucket_start)
                FROM velocity_counters
                WHERE mandate_id = %s AND counter = 'cooldown'
                  AND "window" = 'expiry' AND bucket_start > %s
                """,
                (mandate_id, current),
            )
            row = cursor.fetchone()
        return _utc(row[0]) if row and row[0] is not None else None

    cooldown_until = get_cooldown

    def record_cooldown(
        self,
        mandate_id: str,
        expires_at: datetime,
        *,
        transaction: Any = None,
    ) -> None:
        expiry = _utc(expires_at)
        self._add(mandate_id, "cooldown", "expiry", expiry, 1, transaction=transaction)

    set_cooldown = record_cooldown

    def get_spend_view(
        self,
        mandate_id: str,
        now: datetime,
        *,
        spent_total: Decimal | str | int = Decimal("0.00"),
        reserved_total: Decimal | str | int = Decimal("0.00"),
        txn_count_period: int = 0,
        mandate_status: MandateStatus | str = MandateStatus.ACTIVE,
        transaction: Any = None,
    ) -> SpendView:
        current = _utc(now)
        minute_start = _minute(current - timedelta(seconds=60))
        hour_start = _minute(current - timedelta(hours=1))
        minute_bucket = _minute(current)
        open_bucket = datetime(1970, 1, 1, tzinfo=UTC)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:

            def counter_sum(counter: str, window: str, start: datetime) -> Decimal:
                cursor.execute(
                    self._COUNT,
                    (mandate_id, counter, window, start, minute_bucket),
                )
                row = cursor.fetchone()
                return row[0] if row and row[0] is not None else Decimal("0")

            intents = counter_sum("intents", "1m", minute_start)
            escalations = counter_sum("escalations", "1h", hour_start)
            cursor.execute(
                """
                SELECT COALESCE(val, 0)
                FROM velocity_counters
                WHERE mandate_id = %s AND counter = 'open_authz'
                  AND "window" = 'current' AND bucket_start = %s
                """,
                (mandate_id, open_bucket),
            )
            open_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT MAX(bucket_start)
                FROM velocity_counters
                WHERE mandate_id = %s AND counter = 'cooldown'
                  AND "window" = 'expiry' AND bucket_start > %s
                """,
                (mandate_id, current),
            )
            cooldown_row = cursor.fetchone()
        return SpendView(
            spent_total=spent_total,
            reserved_total=reserved_total,
            txn_count_period=txn_count_period,
            mandate_status=mandate_status,
            intents_last_60s=int(intents),
            escalations_last_hour=int(escalations),
            open_authorizations=max(
                0, int(open_row[0] if open_row and open_row[0] is not None else 0)
            ),
            cooldown_until=(
                _utc(cooldown_row[0]) if cooldown_row and cooldown_row[0] is not None else None
            ),
        )

    spend_view = get_spend_view
    read = get_spend_view


def _pack_idempotency_response(record: IdempotencyRecord) -> str:
    """Serialize to the column's actual declared type.

    `idempotency_keys.response` is TEXT (the agent lane's table, shared
    verbatim — see aval/contracts/fixtures/schema.sql). Handing psycopg a
    raw dict for a TEXT column raises "cannot adapt type 'dict' using
    placeholder '%s'"; the column was never JSONB, so the fix is to
    serialize here rather than to cast the column.
    """
    return json.dumps(
        {
            "_aval_idempotency": {
                "request_fingerprint": record.request_fingerprint,
                "claim_token": record.claim_token,
            },
            "response": record.response,
        }
    )


def _unpack_idempotency_row(row: Any) -> IdempotencyRecord:
    key, scope, stored_response, derived_from, expires_at, created_at = row
    if isinstance(stored_response, str):
        stored_response = json.loads(stored_response)
    metadata: Mapping[str, Any] = stored_response if isinstance(stored_response, Mapping) else {}
    marker = metadata.get("_aval_idempotency", {})
    response = metadata.get("response") if "response" in metadata else stored_response
    fingerprint = str(marker.get("request_fingerprint", ""))
    if not fingerprint:
        raise IdempotencyConflict("stored idempotency row has no request fingerprint")
    return IdempotencyRecord(
        key=key,
        scope=scope,
        derived_from=derived_from,
        request_fingerprint=fingerprint,
        # `expires_at` stays TIMESTAMPTZ (a [2]-only addition — see the
        # canonical schema's header); `created_at` is TEXT (the agent lane's
        # own column on this shared table), so psycopg hands back a str.
        expires_at=_utc(expires_at),
        response=response,
        created_at=_utc(created_at)
        if isinstance(created_at, datetime)
        else _utc(datetime.fromisoformat(created_at)),
        claim_token=marker.get("claim_token"),
    )


class PostgresIdempotencyStore(_PostgresAdapter):
    """Atomic ``idempotency_keys`` adapter with lazy expiry."""

    _COLUMNS = "key, scope, response, derived_from, expires_at, created_at"
    _SELECT = f"SELECT {_COLUMNS} FROM idempotency_keys WHERE key = %s"

    def __init__(
        self,
        connection: Any = None,
        connection_factory: ConnectionFactory | None = None,
        *,
        secret: str | bytes = "local-development-only",
    ) -> None:
        super().__init__(connection, connection_factory)
        self.secret = secret

    def reserve(self, record: IdempotencyRecord, now: datetime | None = None) -> IdempotencyRecord:
        current = _utc(now) if now is not None else datetime.now(UTC)
        expected_key = derive_idempotency_key(record.derived_from, self.secret)
        if not hmac.compare_digest(record.key, expected_key):
            raise IdempotencyConflict("idempotency key must be derived from the source jti")
        candidate = record
        with self._transaction() as connection, self._cursor(connection) as cursor:
            cursor.execute(
                f"""
                INSERT INTO idempotency_keys
                    ({self._COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO NOTHING
                RETURNING {self._COLUMNS}
                """,
                (
                    candidate.key,
                    candidate.scope,
                    _pack_idempotency_response(candidate),
                    candidate.derived_from,
                    candidate.expires_at,
                    _iso(candidate.created_at or current),
                ),
            )
            row = cursor.fetchone()
            if row:
                return _unpack_idempotency_row(row)
            cursor.execute(self._SELECT, (candidate.key,))
            existing_row = cursor.fetchone()
        if existing_row is None:
            raise RuntimeError("idempotency reservation disappeared after conflict")
        existing = _unpack_idempotency_row(existing_row)
        if existing.is_expired(current):
            with self._transaction() as connection, self._cursor(connection) as cursor:
                cursor.execute(
                    "DELETE FROM idempotency_keys WHERE key = %s AND expires_at <= %s",
                    (candidate.key, current),
                )
            return self.reserve(candidate, current)
        if (
            existing.scope != candidate.scope
            or existing.derived_from != candidate.derived_from
            or not hmac.compare_digest(existing.request_fingerprint, candidate.request_fingerprint)
        ):
            raise IdempotencyConflict("same idempotency key used with a different request")
        return existing

    def reserve_for(
        self,
        jti: str,
        scope: str,
        request: Any,
        created_at: datetime,
        *,
        ttl: timedelta = IDEMPOTENCY_TTL,
    ) -> IdempotencyRecord:
        return self.reserve(
            make_record(
                jti,
                self.secret,
                scope,
                request,
                created_at,
                ttl=ttl,
                claim_token=uuid4().hex,
            ),
            created_at,
        )

    def get(self, key: str, now: datetime | None = None) -> IdempotencyRecord | None:
        current = _utc(now) if now is not None else datetime.now(UTC)
        with self._transaction() as connection, self._cursor(connection) as cursor:
            cursor.execute(f"{self._SELECT} AND expires_at > %s", (key, current))
            row = cursor.fetchone()
        return _unpack_idempotency_row(row) if row else None

    def save_response(
        self,
        key: str,
        response: Mapping[str, Any],
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        current_time = _utc(now) if now is not None else datetime.now(UTC)
        with self._transaction() as connection, self._cursor(connection) as cursor:
            cursor.execute(self._SELECT, (key,))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(key)
            current = _unpack_idempotency_row(row)
            if current.is_expired(current_time):
                raise KeyError(key)
            if current.response is not None:
                return current
            updated = replace(current, response=dict(response))
            # response is TEXT, so the JSONB `?`/`->>` guard this used to run
            # doesn't parse. A compare-and-swap on the exact previous text —
            # the same idiom src/agent/kernel.py uses for the money columns —
            # gives back the identical guarantee: this UPDATE only lands if
            # nothing else has touched the row since we read `current`.
            cursor.execute(
                """
                UPDATE idempotency_keys
                SET response = %s
                WHERE key = %s
                  AND response = %s
                  AND expires_at > %s
                RETURNING key, scope, response, derived_from, expires_at, created_at
                """,
                (
                    _pack_idempotency_response(updated),
                    key,
                    _pack_idempotency_response(current),
                    current_time,
                ),
            )
            saved_row = cursor.fetchone()
            if saved_row:
                return _unpack_idempotency_row(saved_row)
            cursor.execute(self._SELECT, (key,))
            existing_row = cursor.fetchone()
        if existing_row is None:
            raise RuntimeError("idempotency response disappeared after update")
        return _unpack_idempotency_row(existing_row)

    def purge_expired(self, now: datetime) -> int:
        current = _utc(now)
        with self._transaction() as connection, self._cursor(connection) as cursor:
            cursor.execute("DELETE FROM idempotency_keys WHERE expires_at <= %s", (current,))
            return int(getattr(cursor, "rowcount", 0) or 0)


class PostgresOutboxWriter(_PostgresAdapter):
    """Append-only outbox adapter that participates in the caller's transaction."""

    def append(self, event: OutboxEvent, *, transaction: Any = None) -> None:
        payload = json.dumps(
            dict(event.payload),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            ensure_ascii=False,
        )
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            cursor.execute(
                """
                INSERT INTO outbox (event_id, type, aggregate_id, payload, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.type,
                    event.aggregate_id,
                    payload,
                    _iso(event.created_at),
                ),
            )

    write = append


def _fmt_money(value: Decimal) -> str:
    """Match `src/agent/crypto/money.py:fmt` exactly — same rounding, same
    string shape — since the two lanes now write the same TEXT column."""
    return str(_money(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class PostgresReservationStore(_PostgresAdapter):
    """Atomic mandate reservation.

    `mandates.reserved_amount`/`spent_total`/`txn_count` are TEXT, not
    NUMERIC (the agent lane's table, shared verbatim — see
    aval/contracts/fixtures/schema.sql's header): Postgres can't do
    `reserved_amount + %s` on a TEXT column, and even if it could, the
    project's whole reason for keeping money as TEXT is to make an UPDATE's
    guard an exact string comparison rather than an arithmetic one. So this
    reserves the same way `src/agent/kernel.py:reserve_chain` does: read the
    row, compute the new value in Python, then guard the UPDATE on every
    value read matching exactly — a compare-and-swap, not `SET x = x + 1`.
    A concurrent writer makes the guard match zero rows, which this reports
    as a lost race rather than retrying (retrying blindly is exactly what
    would let two concurrent callers both believe they reserved).
    """

    def reserve(
        self,
        mandate_id: str,
        amount: Decimal | str,
        total_budget: Decimal | str,
        *,
        max_txn_count: int | None = None,
        reservation_id: str | None = None,
        reservation_key: str | None = None,
        transaction: Any = None,
    ) -> str | None:
        candidate = _money(amount)
        budget = _money(total_budget)
        identifier = reservation_id or str(uuid4())
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            if reservation_key is not None:
                cursor.execute(
                    """
                    SELECT reservation_id FROM purchases
                    WHERE id = %s AND status = 'pending_capture'
                    """,
                    (reservation_key,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    # Already reserved under this key — idempotent replay,
                    # not a new reservation.
                    return str(existing[0]) if existing[0] else None

            cursor.execute(
                "SELECT reserved_amount, spent_total, txn_count, status "
                "FROM mandates WHERE jti = %s",
                (mandate_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            reserved_amount, spent_total, txn_count, mandate_status = row
            if mandate_status != "active":
                return None
            reserved_dec = _money(reserved_amount)
            spent_dec = _money(spent_total)
            if reserved_dec + spent_dec + candidate > budget:
                return None
            if max_txn_count is not None and int(txn_count) + 1 > max_txn_count:
                return None

            cursor.execute(
                """
                UPDATE mandates
                SET reserved_amount = %s, txn_count = %s, updated_at = %s
                WHERE jti = %s AND status = 'active'
                  AND reserved_amount = %s AND spent_total = %s AND txn_count = %s
                RETURNING jti
                """,
                (
                    _fmt_money(reserved_dec + candidate),
                    int(txn_count) + 1,
                    _iso(datetime.now(UTC)),
                    mandate_id,
                    reserved_amount,
                    spent_total,
                    txn_count,
                ),
            )
            won = cursor.fetchone()
        return identifier if won else None

    def release(
        self,
        mandate_id: str,
        amount: Decimal | str,
        reservation_id: str | None = None,
        *,
        transaction: Any = None,
    ) -> bool:
        candidate = _money(amount)
        with self._transaction(transaction) as connection, self._cursor(connection) as cursor:
            if reservation_id is not None:
                cursor.execute(
                    """
                    SELECT 1 FROM purchases
                    WHERE reservation_id = %s
                      AND mandate_jti = %s
                      AND status = 'pending_capture'
                    """,
                    (reservation_id, mandate_id),
                )
                if cursor.fetchone() is None:
                    return False

            cursor.execute(
                "SELECT reserved_amount, txn_count FROM mandates WHERE jti = %s",
                (mandate_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            reserved_amount, txn_count = row
            reserved_dec = _money(reserved_amount)
            if reserved_dec < candidate:
                return False

            cursor.execute(
                """
                UPDATE mandates
                SET reserved_amount = %s, txn_count = %s, updated_at = %s
                WHERE jti = %s AND reserved_amount = %s AND txn_count = %s
                """,
                (
                    _fmt_money(reserved_dec - candidate),
                    max(0, int(txn_count) - 1),
                    _iso(datetime.now(UTC)),
                    mandate_id,
                    reserved_amount,
                    txn_count,
                ),
            )
            return bool(getattr(cursor, "rowcount", 0))


VelocityStorePostgres = PostgresVelocityStore
IdempotencyStorePostgres = PostgresIdempotencyStore
ReservationStorePostgres = PostgresReservationStore


__all__ = [
    "IdempotencyStorePostgres",
    "PostgresOutboxWriter",
    "PostgresIdempotencyStore",
    "PostgresReservationStore",
    "ReservationStorePostgres",
    "VelocityStorePostgres",
    "PostgresVelocityStore",
]
