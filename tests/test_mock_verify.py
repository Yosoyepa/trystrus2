"""The verify mock decides from the fixture — it does not approve everything.

PLAN-PARALELO §6.8 forbids a permissive mock, and this file is what makes that
enforceable rather than aspirational. If someone "simplifies" the mock into
`return APPROVED`, these tests fail.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aval" / "contracts" / "mocks"))

import mock_verify  # noqa: E402

from trustlib import fake  # noqa: E402
from trustlib.models import DecisionOutcome, MandateStatus, ReasonCode  # noqa: E402


@pytest.fixture
def mandate():
    return fake.mandate(max_per_txn="150", total_budget="400", max_txn_count=3)


@pytest.fixture
def offer():
    return fake.offer(offer_id="ofr_COR_130", amount="130.00", category="flights")


def verify(mandate, intent, offer=None, **spend_kwargs):
    return mock_verify.evaluate(
        mandate, intent, mock_verify.spend_from_decimals(**spend_kwargs), offer=offer
    )


# ==========================================================================
# The happy path — and it is genuinely the only one
# ==========================================================================
def test_in_mandate_purchase_is_approved(mandate, offer):
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00")

    decision = verify(mandate, intent, offer)

    assert decision.decision is DecisionOutcome.APPROVED
    assert decision.reservation_id is not None
    assert decision.expires_in == 120


def test_the_mock_is_not_permissive(mandate, offer):
    """The one test that would catch a mock rewritten to always approve."""
    over_limit = fake.intent(mandate_jti=mandate.jti, offer_id="ofr_MIA_300", amount="300.00")
    wrong_category = fake.intent(mandate_jti=mandate.jti, offer_id="ofr_HTL_120", amount="120.00")

    assert (
        verify(mandate, over_limit, fake.offer(offer_id="ofr_MIA_300", amount="300.00")).decision
        is not DecisionOutcome.APPROVED
    )
    assert (
        verify(
            mandate,
            wrong_category,
            fake.offer(offer_id="ofr_HTL_120", amount="120.00", category="hotels"),
        ).decision
        is not DecisionOutcome.APPROVED
    )


# ==========================================================================
# State beats everything else
# ==========================================================================
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (MandateStatus.REVOKED, ReasonCode.MANDATE_REVOKED),
        (MandateStatus.SUSPENDED, ReasonCode.MANDATE_SUSPENDED),
        (MandateStatus.EXHAUSTED, ReasonCode.MANDATE_EXHAUSTED),
        (MandateStatus.EXPIRED, ReasonCode.MANDATE_EXPIRED),
        (MandateStatus.DRAFT, ReasonCode.MANDATE_SUSPENDED),  # unknown -> deny
    ],
)
def test_non_active_mandate_is_refused_whatever_the_amount(mandate, offer, status, expected):
    """A revoked mandate refuses a $1 purchase, not just an expensive one."""
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00")

    decision = verify(mandate, intent, offer, status=status)

    assert decision.decision is DecisionOutcome.REJECTED
    assert decision.reason_code is expected


def test_revocation_wins_over_a_budget_problem(mandate, offer):
    """Order of evaluation: state before money.

    Both faults are present. Reporting BUDGET_EXCEEDED would be true and
    useless -- the buyer revoked, and that is what the trail must say.
    """
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00")

    decision = verify(mandate, intent, offer, spent="399", status=MandateStatus.REVOKED)

    assert decision.reason_code is ReasonCode.MANDATE_REVOKED


# ==========================================================================
# Scope and time
# ==========================================================================
def test_forbidden_category_is_rejected(mandate):
    hotel = fake.offer(offer_id="ofr_HTL_120", amount="120.00", category="hotels")
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=hotel.offer_id, amount="120.00")

    assert verify(mandate, intent, hotel).reason_code is ReasonCode.CATEGORY_FORBIDDEN


def test_merchant_outside_the_allowlist_is_rejected(mandate, offer):
    intent = fake.intent(
        mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00", merchant_id="not-vuelaya"
    )

    assert verify(mandate, intent, offer).reason_code is ReasonCode.MERCHANT_NOT_ALLOWED


def test_expired_and_not_yet_valid_mandates(offer):
    now = datetime.now(UTC)
    intent_kwargs = dict(offer_id=offer.offer_id, amount="130.00")

    expired = fake.mandate(valid_from=now - timedelta(days=10), valid_until=now - timedelta(days=1))
    assert (
        verify(expired, fake.intent(mandate_jti=expired.jti, **intent_kwargs), offer).reason_code
        is ReasonCode.MANDATE_EXPIRED
    )

    future = fake.mandate(valid_from=now + timedelta(days=1), valid_until=now + timedelta(days=10))
    assert (
        verify(future, fake.intent(mandate_jti=future.jti, **intent_kwargs), offer).reason_code
        is ReasonCode.MANDATE_NOT_YET_VALID
    )


# ==========================================================================
# Money
# ==========================================================================
def test_over_per_txn_limit_escalates_rather_than_refusing(mandate):
    """The human-in-the-loop moment: $300 against a $150 limit asks Marta.

    PLAN.md §7 requires this exact scene -- a Telegram message reading
    "out of mandate: $300 > $150" with Approve/Reject buttons.
    """
    big = fake.offer(offer_id="ofr_MIA_300", amount="300.00")
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=big.offer_id, amount="300.00")

    decision = verify(mandate, intent, big)

    assert decision.decision is DecisionOutcome.ESCALATED
    assert decision.reason_code is ReasonCode.AMOUNT_EXCEEDS_PER_TXN
    assert decision.diff == {"limit": "max_per_txn", "value": "150", "attempted": "300.00"}


def test_hard_limits_are_checked_before_the_escalatable_one(mandate):
    """Do not ask a human to approve something that fails anyway.

    Over per-txn AND over budget: approving would send it back through the
    gate (schemas.md §5), which would refuse on budget. Refuse now instead.
    """
    big = fake.offer(offer_id="ofr_MIA_300", amount="300.00")
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=big.offer_id, amount="300.00")

    decision = verify(mandate, intent, big, spent="390")

    assert decision.decision is DecisionOutcome.REJECTED
    assert decision.reason_code is ReasonCode.BUDGET_EXCEEDED


def test_canonical_fixture_states_the_same_threshold_twice():
    """Documents a snag in the frozen fixture, raised with Dev 2.

    schemas.md §9 pins `max_per_txn: 150` *and* the condition
    `offer.price < 150`. They are the same number, so every over-limit
    purchase violates both and escalation on this mandate is a dead end:
    Marta approves, the gate re-runs, the condition still refuses.

    The ordering here keeps the demo scene alive. The real fix is to make the
    ceiling and the buy-trigger different numbers -- a fixture change, which
    is not ours to make alone.
    """
    canonical = fake.mandate(max_per_txn="150", conditions={"<": [{"var": "offer.price"}, 150]})
    big = fake.offer(offer_id="ofr_MIA_300", amount="300.00")

    decision = verify(
        canonical,
        fake.intent(mandate_jti=canonical.jti, offer_id=big.offer_id, amount="300.00"),
        big,
    )

    # Escalates today because limits are evaluated before conditions...
    assert decision.decision is DecisionOutcome.ESCALATED
    # ...but the condition that would refuse it after approval is still there.
    assert canonical.conditions == {"<": [{"var": "offer.price"}, 150]}


def test_separated_ceiling_and_trigger_behave_sensibly():
    """What the fixture should look like: spend up to $200 if asked,
    but only buy unprompted under $150."""
    sane = fake.mandate(max_per_txn="200", conditions={"<": [{"var": "offer.price"}, 150]})
    offer_170 = fake.offer(offer_id="ofr_X_170", amount="170.00")
    intent = fake.intent(mandate_jti=sane.jti, offer_id="ofr_X_170", amount="170.00")

    # Within the ceiling, outside the buy-trigger -> a clean refusal, and
    # approving it would genuinely succeed if the buyer relaxed the trigger.
    assert verify(sane, intent, offer_170).reason_code is ReasonCode.CONDITION_FAILED


def test_budget_counts_reservations_not_just_spend(mandate, offer):
    """An in-flight reservation must consume budget, or two races both pass."""
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00")

    assert (
        verify(mandate, intent, offer, spent="200", reserved="0").decision
        is DecisionOutcome.APPROVED
    )
    assert (
        verify(mandate, intent, offer, spent="200", reserved="130").reason_code
        is ReasonCode.BUDGET_EXCEEDED
    )


def test_transaction_count_exhausts_the_mandate(mandate, offer):
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00")

    assert verify(mandate, intent, offer, count=2).decision is DecisionOutcome.APPROVED
    assert verify(mandate, intent, offer, count=3).reason_code is ReasonCode.LIMIT_EXHAUSTED


def test_budget_boundary_is_inclusive(mandate, offer):
    """Spending exactly the budget is allowed; one cent more is not."""
    intent = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="130.00")

    assert verify(mandate, intent, offer, spent="270").decision is DecisionOutcome.APPROVED
    assert verify(mandate, intent, offer, spent="270.01").reason_code is ReasonCode.BUDGET_EXCEEDED


# ==========================================================================
# Price manipulation and conditions
# ==========================================================================
def test_intent_amount_must_equal_the_catalog_price(mandate, offer):
    """The agent does not choose the amount (schemas.md §2)."""
    lying = fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount="1.00")

    assert verify(mandate, lying, offer).reason_code is ReasonCode.CONDITION_FAILED


def test_jsonlogic_condition_is_evaluated(mandate):
    """`{"<": [{"var": "offer.price"}, 150]}` — the same rule Marta signed."""
    cheap = fake.offer(offer_id="ofr_MDE_95", amount="95.00")
    assert (
        verify(
            mandate,
            fake.intent(mandate_jti=mandate.jti, offer_id=cheap.offer_id, amount="95.00"),
            cheap,
        ).decision
        is DecisionOutcome.APPROVED
    )

    at_the_boundary = fake.offer(offer_id="ofr_X_150", amount="150.00")
    decision = verify(
        mandate,
        fake.intent(mandate_jti=mandate.jti, offer_id="ofr_X_150", amount="150.00"),
        at_the_boundary,
    )
    # 150 is not < 150: the condition fails even though the limit allows it.
    assert decision.reason_code is ReasonCode.CONDITION_FAILED


def test_unsupported_jsonlogic_operator_raises_rather_than_passing():
    """Fail closed: an operator we cannot evaluate must not silently pass."""
    mandate = fake.mandate(conditions={"regex": [{"var": "offer.title"}, ".*"]})
    offer = fake.offer()

    with pytest.raises(ValueError, match="unsupported JsonLogic operator"):
        verify(
            mandate,
            fake.intent(mandate_jti=mandate.jti, offer_id=offer.offer_id, amount=offer.amount),
            offer,
        )
