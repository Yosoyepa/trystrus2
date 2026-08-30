"""Stand-in for `POST /mandates/{id}/verify` — Dev 2's endpoint.

Dev 3's checkout cannot be built or tested before M1 without something to call.
This is that something.

**It decides from the fixture, applying the same decision table as the real
gate.** A mock that approves everything is forbidden (PLAN-PARALELO §6.8) for a
concrete reason: it would let Dev 3 ship a checkout that never exercises the
402 path, and the first time anyone saw a refusal would be during integration.

This is a *test double*, not a second implementation of the gate. When Dev 2's
verify endpoint lands at M1, the same contract tests run against both
(PLAN-PARALELO §6.6) and this file stops being used in the charge path.

Owned by: community. Behaviour changes need Dev 2's agreement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trustlib import ids
from trustlib.models import (
    Decision,
    DecisionOutcome,
    MandateClaims,
    MandateStatus,
    Offer,
    PurchaseIntent,
    ReasonCode,
    SpendView,
)

RESERVATION_TTL_SECONDS = 120


def evaluate(
    mandate: MandateClaims,
    intent: PurchaseIntent,
    spend: SpendView,
    *,
    offer: Offer | None = None,
    now: datetime | None = None,
) -> Decision:
    """Decide APPROVED / REJECTED / ESCALATED with a reason code.

    Evaluation order is a real design choice, not an implementation detail,
    because several rules can fail at once and only one reason reaches the
    buyer. This mock uses:

        state -> validity -> scope -> price match -> budget/count
              -> per-txn limit (ESCALATE) -> conditions

    Two consequences worth knowing:

    * **State beats money.** A revoked mandate refuses a $1 purchase.
      Reporting BUDGET_EXCEEDED for a revoked mandate would be true and
      useless -- the buyer revoked, and that is what the trail must say.

    * **Hard limits are checked before the escalatable one.** Escalating a
      purchase that would still fail on budget after approval wastes the
      human's decision: they say yes, the gate re-runs (schemas.md §5), and
      it fails anyway.

    * **`max_per_txn` is checked before `conditions`.** `limits` bound the
      authority the buyer delegated -- exceeding them is a question a human
      can answer, so it escalates (PLAN.md §7: "$300 > $150" with buttons).
      `conditions` express what the buyer asked the agent to look for; failing
      them is not a question, it is a no.

    OPEN QUESTION FOR DEV 2 (owner of the real gate): this ordering is ours to
    agree on, and the canonical fixture makes it urgent -- see the note in
    `evaluate`'s conditions branch.
    """
    moment = now or datetime.now(UTC)

    # ---- 1. mandate state -------------------------------------------------
    state_refusals = {
        MandateStatus.REVOKED: ReasonCode.MANDATE_REVOKED,
        MandateStatus.SUSPENDED: ReasonCode.MANDATE_SUSPENDED,
        MandateStatus.EXHAUSTED: ReasonCode.MANDATE_EXHAUSTED,
        MandateStatus.EXPIRED: ReasonCode.MANDATE_EXPIRED,
    }
    if spend.mandate_status in state_refusals:
        return _reject(state_refusals[spend.mandate_status])
    if spend.mandate_status != MandateStatus.ACTIVE:
        # draft, or anything we do not recognise -> deny. Fail closed.
        return _reject(ReasonCode.MANDATE_SUSPENDED)

    # ---- 2. validity window ----------------------------------------------
    if moment < mandate.validity.not_before:
        return _reject(ReasonCode.MANDATE_NOT_YET_VALID)
    if moment > mandate.validity.expires_at:
        return _reject(ReasonCode.MANDATE_EXPIRED)

    # ---- 3. scope ---------------------------------------------------------
    if mandate.scope.merchants and intent.merchant_id not in mandate.scope.merchants:
        return _reject(ReasonCode.MERCHANT_NOT_ALLOWED)
    if offer is not None and mandate.scope.categories:
        if offer.category not in mandate.scope.categories:
            return _reject(ReasonCode.CATEGORY_FORBIDDEN)

    # ---- 4. the anti-manipulation invariant -------------------------------
    # The agent does not choose the amount; it must equal the catalog price.
    if offer is not None and intent.amount_decimal != offer.amount_decimal:
        return _reject(ReasonCode.CONDITION_FAILED)

    # ---- 5. hard money limits --------------------------------------------
    # Checked before the escalatable limit: approving something that still
    # fails afterwards spends the human's attention for nothing.
    amount = intent.amount_decimal
    limits = mandate.limits

    if limits.total_budget is not None:
        committed = spend.spent_total + spend.reserved_total + amount
        if committed > limits.total_budget:
            return _reject(ReasonCode.BUDGET_EXCEEDED)

    if limits.max_txn is not None and spend.txn_count_period >= limits.max_txn.count:
        return _reject(ReasonCode.LIMIT_EXHAUSTED)

    # ---- 6. the one case that ASKS instead of refusing --------------------
    # PLAN.md §7: "$300 > $150" reaches Marta's phone with Approve/Reject.
    if limits.max_per_txn is not None and amount > limits.max_per_txn:
        return Decision(
            decision=DecisionOutcome.ESCALATED,
            reason_code=ReasonCode.AMOUNT_EXCEEDS_PER_TXN,
            diff={"limit": "max_per_txn",
                  "value": str(limits.max_per_txn),
                  "attempted": str(amount)},
        )

    # ---- 7. conditions (JsonLogic) ----------------------------------------
    #
    # CONTRACT SNAG, raised with Dev 2 (devlog dev3, 2026-08-29):
    # the canonical fixture states the same threshold twice --
    # `max_per_txn: 150` AND `conditions: {"<": [offer.price, 150]}`.
    # Any purchase over $150 therefore violates both, so whichever rule is
    # evaluated first decides whether Marta is ASKED or simply refused.
    # Worse, escalation on that mandate is a dead end: she approves, the gate
    # re-runs (schemas.md §5), and the condition still says no.
    #
    # We evaluate conditions last so the demo scene works, but the real fix is
    # in the fixture: the ceiling and the buy-trigger should be different
    # numbers (e.g. spend up to $200 if asked, but only buy unasked under
    # $150). Proposed to Dev 2; not changed unilaterally -- §9 froze it.
    if mandate.conditions and offer is not None:
        if not _eval_jsonlogic(mandate.conditions,
                               {"offer": {"price": float(offer.amount_decimal)},
                                "now": moment.isoformat()}):
            return _reject(ReasonCode.CONDITION_FAILED)

    return Decision(
        decision=DecisionOutcome.APPROVED,
        reservation_id=ids.new_id("rsv"),
        expires_in=RESERVATION_TTL_SECONDS,
    )


def _reject(reason: ReasonCode) -> Decision:
    return Decision(decision=DecisionOutcome.REJECTED, reason_code=reason)


def _eval_jsonlogic(rule: dict, context: dict):
    """The tiny JsonLogic subset the mandate conditions use.

    Deliberately minimal: schemas.md §1 restricts conditions to `offer.*` and
    `now`, with no custom functions. Dev 2's gate uses a real JsonLogic
    implementation; this only needs to agree with it on that subset.
    """
    if not isinstance(rule, dict):
        return rule

    (operator, operands), = rule.items()
    if operator == "var":
        path = operands if isinstance(operands, str) else operands[0]
        value = context
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    args = [_eval_jsonlogic(a, context) for a in operands]
    match operator:
        case "<":  return args[0] < args[1]
        case "<=": return args[0] <= args[1]
        case ">":  return args[0] > args[1]
        case ">=": return args[0] >= args[1]
        case "==": return args[0] == args[1]
        case "!=": return args[0] != args[1]
        case "and": return all(args)
        case "or":  return any(args)
        case "!":   return not args[0]
        case "in":  return args[0] in args[1]
        case _:
            raise ValueError(
                f"mock_verify: unsupported JsonLogic operator {operator!r}. "
                "Conditions are limited to offer.* and now (schemas.md §1)."
            )


def spend_from_decimals(spent: str = "0", reserved: str = "0",
                        count: int = 0,
                        status: MandateStatus = MandateStatus.ACTIVE) -> SpendView:
    """Convenience for tests that do not have a ledger."""
    return SpendView(
        spent_total=Decimal(spent),
        reserved_total=Decimal(reserved),
        txn_count_period=count,
        mandate_status=status,
    )
