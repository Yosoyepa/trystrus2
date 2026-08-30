"""Hypothesis properties for DEV2's deterministic T1 policy rules."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from src.api.domain.idempotency import derive_idempotency_key
from src.api.domain.models import (
    BurstState,
    DecisionValue,
    EscalationLevel,
    MandateClaims,
    MandateLimits,
    MandateScope,
    MandateStatus,
    MandateValidity,
    Offer,
    PurchaseIntent,
    ReasonCode,
    SpendView,
)
from src.api.domain.policy import (
    PolicyGate,
    RiskSignal,
    decision_from_signals,
    evaluate_burst,
    evaluate_step_up,
    ttl_for_level,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
settings.register_profile(
    "ci",
    max_examples=200,
    deadline=None,
    derandomize=True,
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))
PROPERTY_SETTINGS = settings()


def _amount_from_cents(cents: int) -> str:
    return format(Decimal(cents) / Decimal("100"), ".2f")


def _valid_mandate() -> MandateClaims:
    return MandateClaims(
        jti="mandate-1",
        agent="agent-1",
        currency="USD",
        scope=MandateScope(categories=("groceries",), merchants=("merchant-1",)),
        limits=MandateLimits(max_per_txn="100.00", total_budget="500.00"),
        validity=MandateValidity(
            not_before=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
        ),
        status=MandateStatus.ACTIVE,
    )


def _valid_intent() -> PurchaseIntent:
    return PurchaseIntent(
        jti="intent-1",
        mandate_jti="mandate-1",
        agent="agent-1",
        merchant_id="merchant-1",
        offer_id="offer-1",
        amount="10.00",
        currency="USD",
        category="groceries",
    )


def _valid_offer() -> Offer:
    return Offer(
        offer_id="offer-1",
        merchant_id="merchant-1",
        category="groceries",
        amount="10.00",
        currency="USD",
    )


@PROPERTY_SETTINGS
@given(
    st.sampled_from(
        (
            "merchant",
            "category",
            "currency",
            "agent",
            "mandate_jti",
            "offer_id",
            "revoked",
        )
    )
)
def test_out_of_mandate_inputs_never_approve(invalid_dimension: str) -> None:
    """Any mismatch with the signed mandate remains a hard rejection."""

    mandate = _valid_mandate()
    intent = _valid_intent()
    offer = _valid_offer()

    if invalid_dimension == "merchant":
        intent = replace(intent, merchant_id="merchant-outside")
    elif invalid_dimension == "category":
        intent = replace(intent, category="electronics")
        offer = replace(offer, category="electronics")
    elif invalid_dimension == "currency":
        intent = replace(intent, currency="EUR")
        offer = replace(offer, currency="EUR")
    elif invalid_dimension == "agent":
        intent = replace(intent, agent="agent-outside", agent_id="agent-outside")
    elif invalid_dimension == "mandate_jti":
        intent = replace(intent, mandate_jti="mandate-outside")
    elif invalid_dimension == "offer_id":
        intent = replace(intent, offer_id="offer-outside")
    else:
        mandate = replace(mandate, status=MandateStatus.REVOKED)

    decision = PolicyGate().evaluate(mandate, intent, SpendView(), NOW, offer)

    assert decision.decision is DecisionValue.REJECTED
    assert decision.is_approved is False


CORROBORATIVE_REASONS = (
    ReasonCode.VELOCITY_BURST,
    ReasonCode.STEPUP_AMOUNT_THRESHOLD,
    ReasonCode.STEPUP_BUDGET_USAGE,
)


@PROPERTY_SETTINGS
@given(st.lists(st.sampled_from(CORROBORATIVE_REASONS), min_size=1, max_size=5))
def test_gold_rule_corroborative_signals_never_reject(
    reasons: list[ReasonCode],
) -> None:
    """Corroborative risk signals escalate but cannot create a rejection."""

    decision = decision_from_signals([RiskSignal(reason) for reason in reasons])

    assert decision.decision is DecisionValue.ESCALATED
    assert decision.is_rejected is False
    assert all(reason in CORROBORATIVE_REASONS for reason in reasons)


@PROPERTY_SETTINGS
@given(st.integers(min_value=1, max_value=100_000))
def test_step_up_amount_threshold_is_inclusive_at_exact_boundary(maximum_cents: int) -> None:
    """The amount threshold is inclusive at exactly 70 percent."""

    maximum_cents *= 10
    boundary_cents = maximum_cents * 7 // 10
    below_boundary_cents = boundary_cents - 1

    exact = evaluate_step_up(
        amount=_amount_from_cents(boundary_cents),
        max_per_txn=_amount_from_cents(maximum_cents),
    )
    below = evaluate_step_up(
        amount=_amount_from_cents(below_boundary_cents),
        max_per_txn=_amount_from_cents(maximum_cents),
    )

    assert exact.required is True
    assert exact.level is EscalationLevel.L3_PLUS
    assert ReasonCode.STEPUP_AMOUNT_THRESHOLD in exact.reasons
    assert below.required is False


@PROPERTY_SETTINGS
@given(st.integers(min_value=5, max_value=100_000))
def test_step_up_budget_threshold_is_inclusive_at_exact_boundary(budget_cents: int) -> None:
    """The budget-usage threshold is inclusive at exactly 80 percent."""

    budget_cents *= 5
    boundary_cents = budget_cents * 4 // 5
    below_boundary_cents = boundary_cents - 1

    exact = evaluate_step_up(
        amount="1.00",
        max_per_txn="100.00",
        spent_total=_amount_from_cents(boundary_cents),
        total_budget=_amount_from_cents(budget_cents),
    )
    below = evaluate_step_up(
        amount="1.00",
        max_per_txn="100.00",
        spent_total=_amount_from_cents(below_boundary_cents),
        total_budget=_amount_from_cents(budget_cents),
    )

    assert exact.required is True
    assert exact.level is EscalationLevel.L3_PLUS
    assert ReasonCode.STEPUP_BUDGET_USAGE in exact.reasons
    assert below.required is False


@PROPERTY_SETTINGS
@given(st.integers(min_value=0, max_value=3))
def test_burst_transition_is_approved_before_limit_and_escalated_at_limit(
    prior_intents: int,
) -> None:
    """Three prior intents admit no fourth intent without escalation."""

    result = evaluate_burst(
        BurstState(intents_in_window=prior_intents),
        now=NOW,
    )

    if prior_intents < 3:
        assert result.decision is DecisionValue.APPROVED
        assert result.cooldown_until is None
    else:
        assert result.decision is DecisionValue.ESCALATED
        assert result.reason_code is ReasonCode.VELOCITY_BURST
        assert result.cooldown_until == NOW + timedelta(minutes=10)


@PROPERTY_SETTINGS
@given(st.integers(min_value=0, max_value=600))
def test_burst_cooldown_rejects_until_expiry(seconds_after_start: int) -> None:
    """A candidate during the ten-minute cooldown is always rejected."""

    cooldown_until = NOW + timedelta(minutes=10)
    result = evaluate_burst(
        BurstState(intents_in_window=0, cooldown_until=cooldown_until),
        now=NOW + timedelta(seconds=seconds_after_start),
    )

    if seconds_after_start < 600:
        assert result.decision is DecisionValue.REJECTED
        assert result.reason_code is ReasonCode.VELOCITY_BURST
    else:
        assert result.decision is DecisionValue.APPROVED


@PROPERTY_SETTINGS
@given(st.sampled_from((EscalationLevel.L3, EscalationLevel.L3_PLUS)))
def test_escalation_levels_keep_their_contract_ttls(level: EscalationLevel) -> None:
    """L3 and L3+ retain the fixed 120-second and 300-second TTLs."""

    expected = 120 if level is EscalationLevel.L3 else 300

    assert ttl_for_level(level) == expected


@PROPERTY_SETTINGS
@given(
    st.text(min_size=1, max_size=80),
    st.binary(min_size=1, max_size=64),
)
def test_idempotency_key_is_stable_for_same_jti_and_secret(
    jti: str,
    secret: bytes,
) -> None:
    """Retries derive exactly the same HMAC key from the same source JTI."""

    first = derive_idempotency_key(jti, secret)
    second = derive_idempotency_key(jti, secret)

    assert first == second
    assert len(first) == 64


@PROPERTY_SETTINGS
@given(
    st.text(min_size=1, max_size=80),
    st.text(min_size=1, max_size=80),
    st.binary(min_size=1, max_size=64),
)
def test_idempotency_key_is_injective_for_distinct_jtis(
    first_jti: str,
    second_jti: str,
    secret: bytes,
) -> None:
    """Distinct intent JTIs cannot share a derived key under one secret."""

    if first_jti == second_jti:
        return

    assert derive_idempotency_key(first_jti, secret) != derive_idempotency_key(second_jti, secret)


@PROPERTY_SETTINGS
@given(st.sampled_from((EscalationLevel.L3, EscalationLevel.L3_PLUS)))
def test_step_up_ttl_matches_escalation_level(level: EscalationLevel) -> None:
    """A generated escalation envelope uses the level's fixed TTL."""

    expected = {EscalationLevel.L3: 120, EscalationLevel.L3_PLUS: 300}[level]
    result = evaluate_step_up(
        amount="1.00",
        max_per_txn="100.00",
        now=NOW,
        first_escalation=level is EscalationLevel.L3_PLUS,
    )

    if level is EscalationLevel.L3_PLUS:
        assert result.ttl_seconds == expected
        assert result.expires_at == NOW + timedelta(seconds=expected)
    else:
        assert ttl_for_level(level) == expected


def test_decimal_threshold_inputs_remain_exact() -> None:
    """The property suite itself must not introduce binary float money."""

    result = evaluate_step_up(
        amount=Decimal("70.00"),
        max_per_txn=Decimal("100.00"),
    )

    assert result.required is True
