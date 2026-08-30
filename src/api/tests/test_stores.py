"""Unit tests for the DEV2 in-memory velocity and idempotency stores."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.api.decision.repository_memory import (
    InMemoryIdempotencyStore,
    InMemoryVelocityStore,
)
from src.api.domain.idempotency import (
    IDEMPOTENCY_TTL,
    IdempotencyConflict,
    derive_idempotency_key,
    make_record,
)

MANDATE_ID = "mandate-001"
SECRET = "test-secret"
START = datetime(2026, 8, 29, 12, 0, 30, tzinfo=UTC)


def test_velocity_store_counts_intents_and_amounts_in_the_rolling_minute() -> None:
    store = InMemoryVelocityStore()

    store.increment_intent(MANDATE_ID, "10.25", START)
    store.increment_intent(MANDATE_ID, Decimal("4.75"), START + timedelta(seconds=20))
    store.increment_intent(MANDATE_ID, "99.99", START + timedelta(minutes=2))

    current = START + timedelta(seconds=20)
    assert store.count_intents(MANDATE_ID, current) == 2
    assert store.amount_sum(MANDATE_ID, current) == Decimal("15.00")

    # The first two events are outside the 60-second window at this point.
    later = START + timedelta(minutes=2)
    assert store.count_intents(MANDATE_ID, later) == 1
    assert store.amount_sum(MANDATE_ID, later) == Decimal("99.99")


def test_velocity_store_keeps_escalations_in_the_hour_window() -> None:
    store = InMemoryVelocityStore()

    store.increment_escalation(MANDATE_ID, START - timedelta(hours=1, minutes=1))
    store.increment_escalation(MANDATE_ID, START - timedelta(minutes=30))
    store.increment_escalation(MANDATE_ID, START)

    assert store.count_escalations(MANDATE_ID, START) == 2
    assert store.count_escalations(MANDATE_ID, START + timedelta(hours=1, minutes=1)) == 0


def test_velocity_store_tracks_open_authorizations_and_never_goes_negative() -> None:
    store = InMemoryVelocityStore()

    store.increment_open_authorizations(MANDATE_ID, START)
    store.increment_open_authorizations(MANDATE_ID, START + timedelta(minutes=1))
    assert store.open_authorizations(MANDATE_ID, START) == 2

    store.decrement_open_authorizations(MANDATE_ID, START)
    store.decrement_open_authorizations(MANDATE_ID, START)
    store.decrement_open_authorizations(MANDATE_ID, START)
    assert store.open_authorizations(MANDATE_ID, START) == 0


def test_velocity_store_exposes_all_counters_through_spend_view() -> None:
    store = InMemoryVelocityStore()
    cooldown = START + timedelta(minutes=5)

    store.increment_intent(MANDATE_ID, "12.50", START)
    store.increment_escalation(MANDATE_ID, START)
    store.increment_open_authorizations(MANDATE_ID, START)
    store.record_cooldown(MANDATE_ID, cooldown)

    spend = store.get_spend_view(
        MANDATE_ID,
        START,
        spent_total="20.00",
        reserved_total="5.00",
        txn_count_period=3,
    )

    assert spend.spent_total == Decimal("20.00")
    assert spend.reserved_total == Decimal("5.00")
    assert spend.txn_count_period == 3
    assert spend.intents_last_60s == 1
    assert spend.escalations_last_hour == 1
    assert spend.open_authorizations == 1
    assert spend.cooldown_until == cooldown


def test_velocity_store_returns_only_the_latest_cooldown_and_expires_it() -> None:
    store = InMemoryVelocityStore()
    earlier = START + timedelta(minutes=2)
    later = START + timedelta(minutes=5)

    store.record_cooldown(MANDATE_ID, later)
    store.record_cooldown(MANDATE_ID, earlier)
    assert store.get_cooldown(MANDATE_ID, START) == later

    assert store.get_cooldown(MANDATE_ID, later) is None
    assert store.get_cooldown(MANDATE_ID, later + timedelta(seconds=1)) is None


def test_idempotency_store_rejects_a_caller_invented_key() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    created_at = START
    valid = make_record("intent-001", SECRET, "verify", {"amount": "10.00"}, created_at)
    forged = replace(valid, key="caller-supplied-key")

    with pytest.raises(IdempotencyConflict, match="derived from the source jti"):
        store.reserve(forged, created_at)


def test_idempotency_store_replay_returns_the_original_record_and_response() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    request = {"amount": "10.00", "currency": "USD"}

    first = store.reserve_for("intent-001", "verify", request, START)
    stored = store.save_response(first.key, {"decision": "APPROVED", "purchase_id": "p-1"})
    replay = store.reserve_for("intent-001", "verify", request, START + timedelta(seconds=1))

    assert replay == stored
    assert replay.response == {"decision": "APPROVED", "purchase_id": "p-1"}
    assert store.get(first.key, START + timedelta(seconds=1)) == stored
    assert first.claim_token


def test_idempotency_store_rejects_response_writes_after_expiry() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    record = store.reserve_for(
        "intent-001",
        "verify",
        {"amount": "10.00"},
        START,
        ttl=timedelta(seconds=1),
    )

    with pytest.raises(KeyError):
        store.save_response(record.key, {"decision": "APPROVED"}, START + timedelta(seconds=1))

    assert store.get(record.key, START + timedelta(seconds=1)) is None


def test_idempotency_store_rejects_same_key_for_a_different_request() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    store.reserve_for("intent-001", "verify", {"amount": "10.00"}, START)

    with pytest.raises(IdempotencyConflict, match="different request"):
        store.reserve_for("intent-001", "verify", {"amount": "11.00"}, START)


def test_idempotency_store_preserves_the_first_response() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    record = store.reserve_for("intent-001", "verify", {"amount": "10.00"}, START)

    first = store.save_response(record.key, {"decision": "REJECTED"})
    second = store.save_response(record.key, {"decision": "APPROVED"})

    assert first.response == {"decision": "REJECTED"}
    assert second == first
    assert store.get(record.key, START).response == {"decision": "REJECTED"}


def test_idempotency_store_uses_the_contract_45_day_ttl() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    record = store.reserve_for("intent-001", "verify", {"amount": "10.00"}, START)

    assert record.expires_at == START + IDEMPOTENCY_TTL
    assert store.get(record.key, START + IDEMPOTENCY_TTL - timedelta(microseconds=1)) == record
    assert store.get(record.key, START + IDEMPOTENCY_TTL) is None


def test_idempotency_store_allows_reclaim_after_expiry() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    first = store.reserve_for("intent-001", "verify", {"amount": "10.00"}, START)
    expiry = START + IDEMPOTENCY_TTL

    replacement = store.reserve_for(
        "intent-001",
        "verify",
        {"amount": "11.00"},
        expiry,
    )

    assert replacement.key == first.key
    assert replacement.request_fingerprint != first.request_fingerprint
    assert store.get(first.key, expiry) == replacement


def test_idempotency_store_purges_only_expired_records() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    expired = make_record(
        "intent-expired",
        SECRET,
        "verify",
        {"amount": "1.00"},
        START,
        ttl=timedelta(days=1),
    )
    active = make_record("intent-active", SECRET, "verify", {"amount": "2.00"}, START)
    store.reserve(expired, START)
    store.reserve(active, START)

    purged = store.purge_expired(START + timedelta(days=1))

    assert purged == 1
    assert store.get(expired.key, START + timedelta(days=1)) is None
    assert store.get(active.key, START + timedelta(days=1)) == active


def test_idempotency_key_is_derived_from_the_intent_jti() -> None:
    store = InMemoryIdempotencyStore(SECRET)
    record = store.reserve_for("intent-001", "verify", {"amount": "10.00"}, START)

    assert record.key == derive_idempotency_key("intent-001", SECRET)
