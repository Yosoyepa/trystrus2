"""Application idempotency claim tests for the DEV2 decision core."""

from datetime import UTC, datetime

from src.api.decision.idempotency import claim_idempotency
from src.api.decision.repository_memory import InMemoryIdempotencyStore

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def test_first_claim_owns_the_derived_pending_record() -> None:
    store = InMemoryIdempotencyStore("test-secret")

    claim = claim_idempotency(
        store,
        jti="intent-001",
        secret="test-secret",
        scope="verify",
        request={"amount": "10.00"},
        now=NOW,
    )

    assert claim.owns_claim is True
    assert claim.record.claim_token
    assert store.get(claim.record.key, NOW) == claim.record


def test_second_equivalent_claim_cannot_repeat_pending_side_effects() -> None:
    store = InMemoryIdempotencyStore("test-secret")
    first = claim_idempotency(
        store,
        jti="intent-001",
        secret="test-secret",
        scope="verify",
        request={"amount": "10.00"},
        now=NOW,
    )

    second = claim_idempotency(
        store,
        jti="intent-001",
        secret="test-secret",
        scope="verify",
        request={"amount": "10.00"},
        now=NOW,
    )

    assert first.owns_claim is True
    assert second.owns_claim is False
    assert second.record == first.record
