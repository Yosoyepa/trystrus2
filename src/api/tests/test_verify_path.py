"""Verify-path tests for the DEV2 gate, reservation, and escalation saga."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from src.api.decision.repository_memory import (
    InMemoryEscalationStore,
    InMemoryIdempotencyStore,
    InMemoryMandateReader,
    InMemoryOfferCatalog,
    InMemoryOutboxWriter,
    InMemoryPurchaseStore,
    InMemoryReservationStore,
    InMemoryVelocityStore,
)
from src.api.decision.service import DecisionService
from src.api.domain.idempotency import IdempotencyConflict
from src.api.domain.models import (
    EscalationLevel,
    MandateClaims,
    MandateLimits,
    MandateScope,
    MandateStatus,
    MandateValidity,
    MaxTxnLimit,
    Offer,
    PurchaseIntent,
    ReasonCode,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class TrustedUV:
    def verify(self, **kwargs: object) -> bool:
        return bool(kwargs.get("assertion"))


def build_harness(
    *,
    intent_amount: str = "10.00",
    offer_amount: str | None = None,
    total_budget: str = "500.00",
) -> dict[str, object]:
    """Build one isolated deterministic verify graph using only in-memory ports."""

    mandate = MandateClaims(
        jti="mandate-1",
        agent="agent-1",
        currency="USD",
        scope=MandateScope(categories=("groceries",), merchants=("merchant-1",)),
        limits=MandateLimits(
            max_per_txn="150.00",
            total_budget=total_budget,
            max_txn=MaxTxnLimit(count=10, period="day"),
        ),
        validity=MandateValidity(
            not_before=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        ),
        status=MandateStatus.ACTIVE,
    )
    offer = Offer(
        offer_id="offer-1",
        merchant_id="merchant-1",
        category="groceries",
        amount=offer_amount or intent_amount,
        currency="USD",
    )
    intent = PurchaseIntent(
        jti="intent-1",
        mandate_jti=mandate.jti,
        agent="agent-1",
        merchant_id=offer.merchant_id,
        offer_id=offer.offer_id,
        amount=intent_amount,
        currency="USD",
        category=offer.category,
    )

    reader = InMemoryMandateReader()
    reader.put(mandate)
    catalog = InMemoryOfferCatalog()
    catalog.put(offer)
    velocity = InMemoryVelocityStore()
    reservation = InMemoryReservationStore()
    reservation.register_mandate(mandate.jti, total_budget=total_budget)
    purchases = InMemoryPurchaseStore()
    escalations = InMemoryEscalationStore()
    outbox = InMemoryOutboxWriter()
    idempotency = InMemoryIdempotencyStore("test-secret")
    service = DecisionService(
        mandate_reader=reader,
        offer_catalog=catalog,
        velocity_store=velocity,
        reservation_store=reservation,
        purchase_store=purchases,
        escalation_store=escalations,
        outbox=outbox,
        idempotency_store=idempotency,
        idempotency_secret="test-secret",
    )
    return {
        "mandate": mandate,
        "intent": intent,
        "offer": offer,
        "reader": reader,
        "catalog": catalog,
        "velocity": velocity,
        "reservation": reservation,
        "purchases": purchases,
        "escalations": escalations,
        "outbox": outbox,
        "idempotency": idempotency,
        "service": service,
    }


def trigger_burst(harness: dict[str, object]) -> None:
    velocity = harness["velocity"]
    for offset in (0, 10, 20):
        velocity.increment_intent("mandate-1", "1.00", NOW + timedelta(seconds=offset))


def test_approved_verify_reserves_and_emits_in_one_decision_branch() -> None:
    harness = build_harness()
    result = harness["service"].verify(harness["intent"], now=NOW)

    assert result.is_approved
    assert result.status == "pending_capture"
    purchase = harness["purchases"].get(result.purchase_id)
    assert purchase is not None
    assert purchase.status == "pending_capture"
    assert purchase.reservation_id == result.reservation_id
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 10
    assert [event.type for event in harness["outbox"].events] == ["purchase.verified"]
    event = harness["outbox"].events[0]
    assert event.event_id
    assert event.aggregate_id == result.purchase_id
    assert event.payload["reservation_id"] == result.reservation_id


def test_rejected_verify_persists_reason_and_never_reserves() -> None:
    harness = build_harness()
    intent = replace(harness["intent"], category="electronics")
    offer = replace(harness["offer"], category="electronics")
    harness["catalog"].put(offer)

    result = harness["service"].verify(intent, now=NOW)

    assert result.is_rejected
    assert result.reason_code is ReasonCode.CATEGORY_FORBIDDEN
    assert harness["purchases"].get(result.purchase_id).status == "rejected"
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0
    assert [event.type for event in harness["outbox"].events] == ["purchase.rejected"]


def test_price_mismatch_is_verdictive_and_does_not_reach_reservation() -> None:
    harness = build_harness(intent_amount="11.00", offer_amount="10.00")

    result = harness["service"].verify(harness["intent"], now=NOW)

    assert result.is_rejected
    assert result.reason_code is ReasonCode.CONDITION_FAILED
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0


def test_burst_escalates_without_reserving_and_records_cooldown() -> None:
    harness = build_harness()
    trigger_burst(harness)

    result = harness["service"].verify(harness["intent"], now=NOW)

    assert result.is_escalated
    assert result.status == "awaiting_escalation"
    assert result.escalation_id is not None
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0
    escalation = harness["escalations"].get(result.escalation_id)
    assert escalation.status == "pending"
    assert escalation.level == "L3"
    assert escalation.diff["ttl_seconds"] == 120
    assert harness["velocity"].get_cooldown("mandate-1", NOW) is not None
    assert [event.type for event in harness["outbox"].events] == [
        "purchase.escalated",
        "agent.paused_cooldown",
    ]


def test_cooldown_rejects_the_next_intent() -> None:
    harness = build_harness()
    trigger_burst(harness)
    first = harness["service"].verify(harness["intent"], now=NOW)
    second_intent = replace(harness["intent"], jti="intent-2")

    result = harness["service"].verify(second_intent, now=NOW + timedelta(seconds=1))

    assert first.is_escalated
    assert result.is_rejected
    assert result.reason_code is ReasonCode.VELOCITY_BURST
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0


def test_escalation_approval_re_gates_changed_mandate_state() -> None:
    harness = build_harness()
    trigger_burst(harness)
    initial = harness["service"].verify(harness["intent"], now=NOW)
    suspended = replace(harness["mandate"], status=MandateStatus.SUSPENDED)
    harness["reader"].put(suspended)

    result = harness["service"].resolve_escalation(
        initial.escalation_id,
        "APPROVE",
        now=NOW + timedelta(seconds=10),
    )

    assert result.is_rejected
    assert result.reason_code is ReasonCode.MANDATE_SUSPENDED
    assert result.status == "compensated"
    assert harness["purchases"].get(initial.purchase_id).status == "compensated"
    assert harness["escalations"].get(initial.escalation_id).status == "expired"
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0


def test_l3_plus_resolution_fails_closed_without_user_verification() -> None:
    harness = build_harness(intent_amount="105.00")
    result = harness["service"].verify(harness["intent"], now=NOW)

    resolved = harness["service"].resolve_escalation(
        result.escalation_id,
        "APPROVE",
        now=NOW + timedelta(seconds=1),
    )

    assert result.is_escalated
    assert result.decision.level.value == "L3+"
    assert result.decision.requires_uv is True
    assert resolved.is_rejected
    assert resolved.status == "compensated"
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0


def test_l3_plus_resolution_uses_trusted_uv_and_re_gates_before_reserving() -> None:
    harness = build_harness(intent_amount="105.00")
    harness["service"].uv_verifier = TrustedUV()
    initial = harness["service"].verify(harness["intent"], now=NOW)

    resolved = harness["service"].resolve_escalation(
        initial.escalation_id,
        "APPROVE",
        now=NOW + timedelta(seconds=1),
        assertion={"user_verified": True},
        uv_verified=True,
    )

    assert initial.decision.level is EscalationLevel.L3_PLUS
    assert resolved.is_approved
    assert resolved.status == "pending_capture"
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 105
    assert harness["escalations"].get(initial.escalation_id).status == "resolved"

    replay = harness["service"].resolve_escalation(
        initial.escalation_id,
        "APPROVE",
        now=NOW + timedelta(seconds=2),
        assertion={"user_verified": True},
        uv_verified=True,
    )
    assert replay.is_approved
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 105


def test_resolution_reloads_the_current_catalog_offer() -> None:
    harness = build_harness()
    trigger_burst(harness)
    initial = harness["service"].verify(harness["intent"], now=NOW)
    changed_offer = replace(harness["offer"], amount="11.00")
    harness["catalog"].put(changed_offer)

    resolved = harness["service"].resolve_escalation(
        initial.escalation_id,
        "APPROVE",
        now=NOW + timedelta(seconds=10),
    )

    assert resolved.is_rejected
    assert resolved.reason_code is ReasonCode.CONDITION_FAILED
    assert resolved.status == "compensated"
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0


def test_expired_escalation_compensates_without_reservation() -> None:
    harness = build_harness()
    trigger_burst(harness)
    initial = harness["service"].verify(harness["intent"], now=NOW)
    escalation = harness["escalations"].get(initial.escalation_id)

    result = harness["service"].resolve_escalation(
        initial.escalation_id,
        "APPROVE",
        now=escalation.timeout_at + timedelta(seconds=1),
    )

    assert result.is_rejected
    assert result.reason_code is ReasonCode.ESCALATION_TIMEOUT_DENIED
    assert result.status == "compensated"
    assert harness["purchases"].get(initial.purchase_id).status == "compensated"


def test_outbox_failure_releases_a_reservation_and_closes_the_purchase() -> None:
    harness = build_harness()
    harness["outbox"].fail = True

    result = harness["service"].verify(harness["intent"], now=NOW)

    assert result.is_rejected
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0
    purchase = harness["purchases"].get(result.purchase_id)
    assert purchase is not None
    assert purchase.status == "rejected"
    assert purchase.reservation_id is None


def test_atomic_reservation_zero_rows_becomes_a_budget_rejection() -> None:
    harness = build_harness(total_budget="5.00")
    result = harness["service"].verify(harness["intent"], now=NOW)

    assert result.is_rejected
    assert result.reason_code is ReasonCode.BUDGET_EXCEEDED
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0


def test_verify_replays_the_first_idempotent_response_without_new_side_effects() -> None:
    harness = build_harness()

    first = harness["service"].verify(harness["intent"], now=NOW)
    second = harness["service"].verify(harness["intent"], now=NOW + timedelta(seconds=1))

    assert first.as_dict() == second.as_dict()
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 10
    assert len(harness["outbox"].events) == 1


def test_verify_rejects_an_idempotency_body_conflict_before_side_effects() -> None:
    harness = build_harness()
    harness["service"].verify(harness["intent"], now=NOW)
    changed = replace(harness["intent"], amount="11.00")

    with pytest.raises(IdempotencyConflict):
        harness["service"].verify(changed, now=NOW + timedelta(seconds=1))

    assert len(harness["outbox"].events) == 1


def test_verify_fails_closed_when_an_equivalent_idempotency_claim_is_pending() -> None:
    harness = build_harness()
    harness["idempotency"].reserve_for(
        "intent-1",
        "verify",
        harness["intent"],
        NOW,
    )

    result = harness["service"].verify(harness["intent"], now=NOW)

    assert result.is_rejected
    assert result.reason_code is ReasonCode.RAIL_ERROR
    assert harness["reservation"].mandates["mandate-1"]["reserved_total"] == 0
    assert harness["outbox"].events == []
