"""Extraction sanity suite for the Dev 2 domain core (R-PRICE/BURST/STEPUP).

These are the seed cases of T20/T22/T23 (decision 0022) plus the gold rule
and the R-IDEM derivation invariants. They exist to prove the extracted
``src/api/domain`` package is self-contained and behaves per contracts v1.1;
the full T-series (property-based, wiring against velocity_counters, verify
path integration) is still Dev 2's pending work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from src.api.domain.idempotency import (
    IDEMPOTENCY_TTL,
    IdempotencyConflict,
    derive_idempotency_key,
    make_record,
    validate_reuse,
)
from src.api.domain.models import EscalationLevel, ReasonCode
from src.api.domain.policy import (
    FailClosedUVVerifier,
    RiskSignal,
    decision_from_signals,
    evaluate_burst,
    evaluate_step_up,
    price_check,
    ttl_for_level,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


# ── T20 (núcleo): integridad de monto punta a punta ─────────────────────────


def test_t20_price_match_approves() -> None:
    assert price_check("130.00", "130.00").is_rejected is False


def test_t20_price_mismatch_rejects_verdictively() -> None:
    decision = price_check("130.00", "150.00")
    assert decision.is_rejected is True
    assert decision.reason_code is ReasonCode.CONDITION_FAILED


def test_t20_float_money_is_refused() -> None:
    decision = price_check(130.0, "130.00")  # type: ignore[arg-type]
    assert decision.is_rejected is True


# ── T22 (núcleo): anti-burst + cooldown ─────────────────────────────────────


def test_t22_third_intent_in_window_still_passes() -> None:
    result = evaluate_burst(now=NOW, intents_in_window=2)
    assert result.decision.value == "APPROVED"


def test_t22_fourth_intent_escalates_and_sets_cooldown() -> None:
    result = evaluate_burst(now=NOW, intents_in_window=3)
    assert result.decision.value == "ESCALATED"
    assert result.reason_code is ReasonCode.VELOCITY_BURST
    assert result.cooldown_until == NOW + timedelta(minutes=10)


def test_t22_intent_during_cooldown_is_rejected() -> None:
    result = evaluate_burst(
        now=NOW + timedelta(seconds=30),
        intents_in_window=5,
        cooldown_until=NOW + timedelta(minutes=10),
    )
    assert result.decision.value == "REJECTED"
    assert result.reason_code is ReasonCode.VELOCITY_BURST


def test_t22_escalation_flood_auto_suspends() -> None:
    result = evaluate_burst(now=NOW, escalations_in_hour=6)
    assert result.decision.value == "REJECTED"
    assert result.reason_code is ReasonCode.MANDATE_SUSPENDED
    assert result.auto_suspend is True


def test_t22_open_authorization_cap_escalates() -> None:
    result = evaluate_burst(now=NOW, open_authorizations=3)
    assert result.decision.value == "ESCALATED"


def test_t22_now_is_mandatory_fail_closed() -> None:
    with pytest.raises(ValueError):
        evaluate_burst()


# ── T23 (núcleo): step-up por umbral fijo + TTL por nivel ───────────────────


def test_t23_amount_at_ratio_requires_l3_plus() -> None:
    result = evaluate_step_up(amount="105.00", max_per_txn="150.00", now=NOW)
    assert result.required is True
    assert result.level is EscalationLevel.L3_PLUS
    assert result.requires_uv is True
    assert ReasonCode.STEPUP_AMOUNT_THRESHOLD in result.reasons


def test_t23_amount_below_ratio_does_not_step_up() -> None:
    result = evaluate_step_up(amount="104.99", max_per_txn="150.00", now=NOW)
    assert result.required is False


def test_t23_budget_usage_at_ratio_requires_l3_plus() -> None:
    result = evaluate_step_up(
        amount="10.00",
        max_per_txn="150.00",
        spent_total="320.00",
        reserved_total="0.00",
        total_budget="400.00",
        now=NOW,
    )
    assert result.required is True
    assert ReasonCode.STEPUP_BUDGET_USAGE in result.reasons


def test_t23_ttls_match_decision_0021() -> None:
    assert ttl_for_level(EscalationLevel.L3) == 120
    assert ttl_for_level("L3+") == 300


def test_t23_uv_stub_fails_closed() -> None:
    assert FailClosedUVVerifier().verify(assertion={"id": "anything"}) is False


# ── Regla de oro: corroborativas escalan, verdictivas rechazan ──────────────


def test_gold_rule_corroborative_only_escalates() -> None:
    decision = decision_from_signals([RiskSignal(ReasonCode.VELOCITY_BURST)])
    assert decision.is_rejected is False
    assert decision.decision.value == "ESCALATED"


def test_gold_rule_verdictive_wins_over_corroborative() -> None:
    decision = decision_from_signals(
        [
            RiskSignal(ReasonCode.VELOCITY_BURST),
            RiskSignal(ReasonCode.MANDATE_REVOKED),
        ]
    )
    assert decision.is_rejected is True
    assert decision.reason_code is ReasonCode.MANDATE_REVOKED


# ── R-IDEM (núcleo): clave derivada y reuso validado ────────────────────────


def test_r19_derived_key_is_stable_and_jti_bound() -> None:
    key = derive_idempotency_key("int_01", "s3cr3t")
    assert key == derive_idempotency_key("int_01", "s3cr3t")
    assert len(key) == 64
    assert key != derive_idempotency_key("int_02", "s3cr3t")


def test_r19_record_keeps_45_day_retention() -> None:
    record = make_record(
        jti="int_01",
        secret="s3cr3t",
        scope="capture:p1",
        request={"amount": "130.00"},
        created_at=NOW,
    )
    assert record.expires_at - NOW == IDEMPOTENCY_TTL == timedelta(days=45)


def test_r19_retry_with_same_body_is_valid() -> None:
    record = make_record(
        jti="int_01",
        secret="s3cr3t",
        scope="capture:p1",
        request={"amount": "130.00"},
        created_at=NOW,
    )
    assert (
        validate_reuse(
            record,
            "int_01",
            "s3cr3t",
            "capture:p1",
            {"amount": "130.00"},
            NOW + timedelta(hours=1),
        )
        is True
    )


def test_r19_same_key_different_body_conflicts() -> None:
    record = make_record(
        jti="int_01",
        secret="s3cr3t",
        scope="capture:p1",
        request={"amount": "130.00"},
        created_at=NOW,
    )
    with pytest.raises(IdempotencyConflict):
        validate_reuse(
            record,
            "int_01",
            "s3cr3t",
            "capture:p1",
            {"amount": "999.00"},
            NOW + timedelta(hours=1),
        )


def test_r19_expired_record_allows_fresh_one() -> None:
    record = make_record(
        jti="int_01",
        secret="s3cr3t",
        scope="capture:p1",
        request={"amount": "130.00"},
        created_at=NOW,
    )
    assert (
        validate_reuse(
            record,
            "int_01",
            "s3cr3t",
            "capture:p1",
            {"amount": "130.00"},
            NOW + timedelta(days=46),
        )
        is False
    )
