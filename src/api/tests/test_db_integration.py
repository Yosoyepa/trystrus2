"""DEV2 PostgreSQL integration checks.

These tests require an explicitly configured database.  They never create or
alter schema objects and use unique identifiers so they can run against a
shared development database.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from src.api.decision.ports import OutboxEvent
from src.api.decision.repository_postgres import (
    PostgresIdempotencyStore,
    PostgresOutboxWriter,
    PostgresReservationStore,
    PostgresVelocityStore,
)
from src.api.domain.idempotency import make_record

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL is not configured", allow_module_level=True)

try:
    import psycopg as postgres_driver
except ModuleNotFoundError:
    postgres_driver = pytest.importorskip("psycopg2", reason="psycopg or psycopg2 is not installed")


@pytest.fixture
def database_connection():
    """Yield a connection and roll back the fixture's transaction when possible."""

    connection = postgres_driver.connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.mark.db
def test_dev2_tables_are_available(database_connection) -> None:
    """The DEV2-owned tables must be present before adapter tests run."""

    with database_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT to_regclass('public.velocity_counters'),
                   to_regclass('public.idempotency_keys'),
                   to_regclass('public.mandates'),
                   to_regclass('public.outbox')
            """
        )
        velocity_table, idempotency_table, mandates_table, outbox_table = cursor.fetchone()

    assert velocity_table == "velocity_counters"
    assert idempotency_table == "idempotency_keys"
    assert mandates_table == "mandates"
    assert outbox_table == "outbox"


@pytest.mark.db
def test_velocity_counter_upsert_and_read(database_connection) -> None:
    """The PostgreSQL velocity adapter persists both count and amount atomically."""

    mandate_id = f"db-test-{uuid4()}"
    now = datetime.now(UTC).replace(microsecond=0)
    store = PostgresVelocityStore(connection=database_connection)

    try:
        store.increment_intent(mandate_id, Decimal("12.34"), now, transaction=database_connection)
        store.increment_intent(mandate_id, Decimal("7.66"), now, transaction=database_connection)

        assert store.count_intents(mandate_id, now, transaction=database_connection) == 2
        assert store.amount_sum(mandate_id, now, transaction=database_connection) == Decimal(
            "20.00"
        )
    finally:
        with database_connection.cursor() as cursor:
            cursor.execute("DELETE FROM velocity_counters WHERE mandate_id = %s", (mandate_id,))


@pytest.mark.db
def test_idempotency_key_round_trip(database_connection) -> None:
    """The PostgreSQL idempotency adapter stores and retrieves a derived key."""

    jti = f"db-test-{uuid4()}"
    created_at = datetime.now(UTC).replace(microsecond=0)
    store = PostgresIdempotencyStore(
        connection=database_connection, secret="db-integration-test-secret"
    )
    record = make_record(
        jti,
        store.secret,
        "verify",
        {"amount": "20.00", "currency": "USD"},
        created_at,
    )

    stored = store.reserve(record)
    loaded = store.get(stored.key, created_at)

    assert loaded is not None
    assert loaded.key == record.key
    assert loaded.derived_from == jti
    assert loaded.request_fingerprint == record.request_fingerprint

    with database_connection.cursor() as cursor:
        cursor.execute("DELETE FROM idempotency_keys WHERE key = %s", (record.key,))


@pytest.mark.db
def test_reservation_update_allows_only_one_competing_connection(database_connection) -> None:
    """The conditional mandate update remains atomic under a real race."""

    mandate_id = f"db-race-{uuid4()}"
    with database_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO mandates
                (id, jti, user_id, agent_id, status, claims, reserved_amount, spent_total,
                 txn_count_period)
            VALUES (%s, %s, %s, %s, 'active', '{}'::jsonb, 0, 0, 0)
            """,
            (mandate_id, mandate_id, "db-test-user", "db-test-agent"),
        )
    database_connection.commit()

    def reserve_once() -> str | None:
        store = PostgresReservationStore(
            connection_factory=lambda: postgres_driver.connect(DATABASE_URL)
        )
        return store.reserve(mandate_id, Decimal("60.00"), Decimal("100.00"))

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve_once) for _ in range(2)]
            results = [future.result() for future in futures]

        assert sum(result is not None for result in results) == 1
        with database_connection.cursor() as cursor:
            cursor.execute(
                "SELECT reserved_amount, txn_count_period FROM mandates WHERE id = %s",
                (mandate_id,),
            )
            reserved_amount, txn_count_period = cursor.fetchone()
        assert reserved_amount == Decimal("60.00")
        assert txn_count_period == 1
    finally:
        with database_connection.cursor() as cursor:
            cursor.execute("DELETE FROM mandates WHERE id = %s", (mandate_id,))
        database_connection.commit()


@pytest.mark.db
def test_outbox_append_rolls_back_with_the_business_transaction(database_connection) -> None:
    """An outbox row passed the transaction handle is removed on rollback."""

    event_id = f"db-rollback-{uuid4()}"
    event = OutboxEvent(
        event_id=event_id,
        type="purchase.verified",
        aggregate_id="db-test-purchase",
        payload={"reservation_id": "db-test-reservation"},
        created_at=datetime.now(UTC).replace(microsecond=0),
    )
    writer = PostgresOutboxWriter(connection=database_connection)

    writer.append(event, transaction=database_connection)
    database_connection.rollback()

    with database_connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM outbox WHERE event_id = %s", (event_id,))
        assert cursor.fetchone()[0] == 0
