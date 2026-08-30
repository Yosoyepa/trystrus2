"""Deterministic policy rules for Aval.

This module is intentionally a pure domain module.  It has no clock, network,
database, web framework, or model dependency.  Callers provide ``now`` and a
read-only spend projection; the same inputs always produce the same decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .models import (
    AmountFormatError,
    AmountLike,
    BurstConfig,
    BurstDecision,
    BurstState,
    Decision,
    DecisionValue,
    Escalation,
    EscalationLevel,
    MandateClaims,
    MandateLimits,
    MandateScope,
    MandateStatus,
    Offer,
    PurchaseIntent,
    ReasonCode,
    SpendView,
    StepUpConfig,
    StepUpDecision,
    amount_decimal,
    canonical_amount,
    ensure_utc,
)

VERDICTIVE_REASONS = frozenset(
    {
        ReasonCode.AMOUNT_EXCEEDS_PER_TXN,
        ReasonCode.BUDGET_EXCEEDED,
        ReasonCode.LIMIT_EXHAUSTED,
        ReasonCode.CATEGORY_FORBIDDEN,
        ReasonCode.MERCHANT_NOT_ALLOWED,
        ReasonCode.MANDATE_EXPIRED,
        ReasonCode.MANDATE_NOT_YET_VALID,
        ReasonCode.MANDATE_REVOKED,
        ReasonCode.MANDATE_SUSPENDED,
        ReasonCode.MANDATE_EXHAUSTED,
        ReasonCode.CONDITION_FAILED,
        ReasonCode.INVALID_SIGNATURE,
        ReasonCode.INVALID_PROOF_OF_POSSESSION,
        ReasonCode.DUPLICATE_JTI,
        ReasonCode.NONCE_REUSED,
        ReasonCode.ESCALATION_TIMEOUT_DENIED,
        ReasonCode.RAIL_ERROR,
        ReasonCode.RAIL_TOKEN_DELETED,
        ReasonCode.PRICE_MISMATCH_AUTO_REFUND,
        ReasonCode.WEBHOOK_INVALID,
    }
)

CORROBORATIVE_REASONS = frozenset(
    {
        ReasonCode.VELOCITY_BURST,
        ReasonCode.STEPUP_AMOUNT_THRESHOLD,
        ReasonCode.STEPUP_BUDGET_USAGE,
    }
)


def _reason(value: ReasonCode | str) -> ReasonCode:
    return value if isinstance(value, ReasonCode) else ReasonCode(value)


def is_verdictive(reason_code: ReasonCode | str) -> bool:
    """Return whether a reason is allowed to produce ``REJECTED``.

    The classification is deliberately conservative: an unknown reason is
    not treated as a verdictive signal by this helper, but the gate itself
    never accepts unknown reason codes because ``ReasonCode`` is closed.
    """

    return _reason(reason_code) in VERDICTIVE_REASONS


def is_corroborative(reason_code: ReasonCode | str) -> bool:
    return _reason(reason_code) in CORROBORATIVE_REASONS


@dataclass(frozen=True, slots=True)
class RiskSignal:
    """A policy signal with an optional local verdictive override.

    ``verdictive=True`` is used for a deterministic cooldown violation.  The
    same ``VELOCITY_BURST`` code is corroborative when it merely observes the
    initial burst and therefore escalates instead of rejecting.
    """

    reason_code: ReasonCode | str
    diff: Mapping[str, Any] | None = None
    verdictive: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_code", _reason(self.reason_code))


def decision_from_signals(signals: Sequence[RiskSignal]) -> Decision:
    """Apply the gold rule when multiple deterministic signals coexist."""

    normalized = tuple(signals)
    for signal in normalized:
        verdictive = signal.verdictive is True or is_verdictive(signal.reason_code)
        if verdictive:
            return Decision(
                DecisionValue.REJECTED,
                signal.reason_code,
                diff=signal.diff,
            )

    if normalized:
        signal = normalized[0]
        return Decision(
            DecisionValue.ESCALATED,
            signal.reason_code,
            diff=signal.diff,
            level=EscalationLevel.L3,
            ttl_seconds=120,
        )
    return Decision(DecisionValue.APPROVED)


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise AmountFormatError("money must not use bool or float")
    if isinstance(value, (str, Decimal, int)):
        return amount_decimal(value)
    raise AmountFormatError("unsupported money value")


def _as_scope(scope: MandateScope | Mapping[str, Any]) -> MandateScope:
    if isinstance(scope, MandateScope):
        return scope
    if isinstance(scope, Mapping):
        return MandateScope(**scope)
    raise ValueError("invalid mandate scope")


def _as_offer_mapping(offer: Offer | Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if offer is None:
        return None
    if isinstance(offer, Offer):
        return {
            "offer_id": offer.offer_id,
            "merchant_id": offer.merchant_id,
            "category": offer.category,
            "amount": offer.amount,
            # The contract's JsonLogic examples call the field price.
            "price": offer.amount,
            "currency": offer.currency,
            "title": offer.title,
            "description": offer.description,
        }
    if isinstance(offer, Mapping):
        result = dict(offer)
        if "amount" in result and "price" not in result:
            result["price"] = result["amount"]
        if "price" in result and "amount" not in result:
            result["amount"] = result["price"]
        return result
    raise ValueError("invalid offer")


def price_matches(intent_amount: AmountLike, offer_amount: AmountLike) -> bool:
    """Compare amounts exactly at cent precision, without float conversion."""

    try:
        return canonical_amount(intent_amount) == canonical_amount(offer_amount)
    except AmountFormatError:
        return False


def compare_price(intent_amount: AmountLike, offer_amount: AmountLike) -> bool:
    """Alias for callers that use the R-PRICE vocabulary."""

    return price_matches(intent_amount, offer_amount)


def price_check(
    intent_amount: AmountLike,
    offer_amount: AmountLike,
) -> Decision:
    """Return a verdictive decision for an amount/offer mismatch."""

    if price_matches(intent_amount, offer_amount):
        return Decision(DecisionValue.APPROVED)
    diff: dict[str, Any] = {"field": "amount"}
    try:
        diff["attempted"] = canonical_amount(intent_amount)
    except AmountFormatError:
        diff["attempted"] = str(intent_amount)
    try:
        diff["offer"] = canonical_amount(offer_amount)
    except AmountFormatError:
        diff["offer"] = str(offer_amount)
    return Decision(DecisionValue.REJECTED, ReasonCode.CONDITION_FAILED, diff=diff)


def scope_allows(
    scope: MandateScope | Mapping[str, Any],
    merchant_id: str,
    category: str,
) -> bool:
    """Check both allow-lists by exact, case-sensitive membership.

    An empty or missing allow-list is denied.  A mandate is a bounded
    capability, so absence of an explicit scope must not become a wildcard.
    """

    try:
        normalized = _as_scope(scope)
    except (TypeError, ValueError):
        return False
    return (
        bool(merchant_id)
        and bool(category)
        and merchant_id in normalized.merchants
        and category in normalized.categories
    )


def mandate_state_reason(
    mandate: MandateClaims | Mapping[str, Any] | MandateStatus | str,
    now: datetime,
) -> ReasonCode | None:
    """Return the hard reason, if any, for a mandate that cannot be used."""

    try:
        current = ensure_utc(now)
    except ValueError:
        return ReasonCode.MANDATE_SUSPENDED

    status_value = _value(
        mandate, "status", mandate if isinstance(mandate, (MandateStatus, str)) else None
    )
    try:
        status = (
            status_value if isinstance(status_value, MandateStatus) else MandateStatus(status_value)
        )
    except (TypeError, ValueError):
        return ReasonCode.MANDATE_SUSPENDED

    status_reasons = {
        MandateStatus.DRAFT: ReasonCode.MANDATE_NOT_YET_VALID,
        MandateStatus.SUSPENDED: ReasonCode.MANDATE_SUSPENDED,
        MandateStatus.REVOKED: ReasonCode.MANDATE_REVOKED,
        MandateStatus.EXPIRED: ReasonCode.MANDATE_EXPIRED,
        MandateStatus.EXHAUSTED: ReasonCode.MANDATE_EXHAUSTED,
    }
    if status is not MandateStatus.ACTIVE:
        return status_reasons[status]

    validity = _value(mandate, "validity")
    if validity is None:
        return ReasonCode.MANDATE_NOT_YET_VALID
    not_before = _value(validity, "not_before")
    expires_at = _value(validity, "expires_at")
    try:
        not_before = ensure_utc(not_before)
        expires_at = ensure_utc(expires_at)
    except (TypeError, ValueError):
        return ReasonCode.MANDATE_SUSPENDED
    if expires_at <= not_before:
        return ReasonCode.MANDATE_EXPIRED
    if current < not_before:
        return ReasonCode.MANDATE_NOT_YET_VALID
    if current >= expires_at:
        return ReasonCode.MANDATE_EXPIRED
    return None


def mandate_is_active(
    mandate: MandateClaims | Mapping[str, Any],
    now: datetime,
) -> bool:
    return mandate_state_reason(mandate, now) is None


def _lookup_var(path: str, context: Mapping[str, Any]) -> Any:
    if path == "now":
        return context["now"]
    if path.startswith("offer."):
        current: Any = context["offer"]
        for part in path.removeprefix("offer.").split("."):
            if not isinstance(current, Mapping) or part not in current:
                raise KeyError(path)
            current = current[part]
        return current
    raise KeyError(path)


def _numeric(value: Any) -> Decimal | None:
    try:
        if isinstance(value, bool) or isinstance(value, float):
            return None
        if isinstance(value, (str, Decimal, int)):
            return _as_decimal(value)
    except AmountFormatError:
        return None
    return None


def _comparison(left: Any, right: Any, operator: str) -> bool:
    left_number = _numeric(left)
    right_number = _numeric(right)
    if left_number is not None and right_number is not None:
        left, right = left_number, right_number
    if operator in {"==", "===", "=", "eq"}:
        return left == right
    if operator in {"!=", "!==", "neq"}:
        return left != right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    raise ValueError(f"unsupported comparison operator: {operator}")


def _jsonlogic(expression: Any, context: Mapping[str, Any]) -> Any:
    if expression is None or isinstance(expression, (str, int, bool, Decimal)):
        return expression
    if isinstance(expression, float):
        raise ValueError("float literals are not permitted in domain conditions")
    if isinstance(expression, list):
        return [_jsonlogic(item, context) for item in expression]
    if not isinstance(expression, Mapping) or len(expression) != 1:
        raise ValueError("JsonLogic expression must have one operator")

    operator, raw_args = next(iter(expression.items()))
    args = raw_args if isinstance(raw_args, list) else [raw_args]
    if operator == "var":
        path = args[0] if args else ""
        if not isinstance(path, str):
            raise ValueError("var path must be a string")
        try:
            return _lookup_var(path, context)
        except KeyError:
            return _jsonlogic(args[1], context) if len(args) > 1 else None

    if operator == "if" or operator == "?:":
        if len(args) < 2:
            raise ValueError("if requires a condition and a value")
        for index in range(0, len(args) - 1, 2):
            if _jsonlogic(args[index], context):
                return _jsonlogic(args[index + 1], context)
        return _jsonlogic(args[-1], context) if len(args) % 2 else None

    if operator == "and":
        result: Any = None
        for arg in args:
            result = _jsonlogic(arg, context)
            if not result:
                return result
        return result
    if operator == "or":
        result = None
        for arg in args:
            result = _jsonlogic(arg, context)
            if result:
                return result
        return result
    if operator in {"!", "!!"}:
        result = not bool(_jsonlogic(args[0], context)) if args else True
        return result if operator == "!" else not result
    if operator in {"==", "===", "!=", "!==", "=", "eq", "neq", "<", "<=", ">", ">="}:
        if len(args) != 2:
            raise ValueError("comparison requires two operands")
        return _comparison(_jsonlogic(args[0], context), _jsonlogic(args[1], context), operator)
    if operator == "in":
        if len(args) != 2:
            raise ValueError("in requires two operands")
        needle = _jsonlogic(args[0], context)
        haystack = _jsonlogic(args[1], context)
        return needle in haystack
    if operator == "missing":
        missing = []
        for arg in args:
            try:
                _lookup_var(arg, context)
            except KeyError:
                missing.append(arg)
        return missing
    if operator == "missing_some":
        if len(args) != 2:
            raise ValueError("missing_some requires two operands")
        minimum = int(_jsonlogic(args[0], context))
        paths = _jsonlogic(args[1], context)
        missing = [path for path in paths if _lookup_missing(path, context)]
        return [] if len(paths) - len(missing) >= minimum else missing
    if operator in {"+", "-", "*", "/", "%", "min", "max"}:
        values = [_jsonlogic(arg, context) for arg in args]
        numbers = [_numeric(value) for value in values]
        if any(value is None for value in numbers):
            raise ValueError("arithmetic operands must be exact decimal values")
        exact = [value for value in numbers if value is not None]
        if operator == "+":
            return sum(exact, Decimal(0))
        if operator == "-":
            return exact[0] if len(exact) == 1 else exact[0] - sum(exact[1:], Decimal(0))
        if operator == "*":
            result = Decimal(1)
            for value in exact:
                result *= value
            return result
        if operator == "/":
            if len(exact) != 2 or exact[1] == 0:
                raise ValueError("division requires two non-zero-safe operands")
            return exact[0] / exact[1]
        if operator == "%":
            if len(exact) != 2 or exact[1] == 0:
                raise ValueError("modulo requires two operands")
            return exact[0] % exact[1]
        return (min if operator == "min" else max)(exact)
    if operator == "cat":
        return "".join(str(_jsonlogic(arg, context)) for arg in args)
    if operator == "merge":
        merged: list[Any] = []
        for arg in args:
            value = _jsonlogic(arg, context)
            merged.extend(value if isinstance(value, list) else [value])
        return merged
    raise ValueError(f"unsupported JsonLogic operator: {operator}")


def _lookup_missing(path: Any, context: Mapping[str, Any]) -> bool:
    try:
        _lookup_var(path, context)
    except (KeyError, TypeError):
        return True
    return False


def evaluate_conditions(
    conditions: Mapping[str, Any] | None,
    offer: Offer | Mapping[str, Any] | None,
    now: datetime,
) -> bool:
    """Evaluate the signed mandate condition with a deliberately tiny surface."""

    if not conditions:
        return True
    offer_mapping = _as_offer_mapping(offer)
    if offer_mapping is None:
        return False
    try:
        current = ensure_utc(now)
        context = {
            "offer": offer_mapping,
            "now": current.isoformat().replace("+00:00", "Z"),
        }
        return bool(_jsonlogic(conditions, context))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return False


def _limits_decision(
    limits: MandateLimits | Mapping[str, Any] | None,
    intent_amount: AmountLike,
    spend: SpendView | Mapping[str, Any],
) -> Decision:
    if limits is None:
        return Decision(DecisionValue.REJECTED, ReasonCode.LIMIT_EXHAUSTED)
    try:
        normalized = limits if isinstance(limits, MandateLimits) else MandateLimits(**limits)
        amount = _as_decimal(intent_amount)
        spent = _as_decimal(_value(spend, "spent_total", Decimal("0.00")))
        reserved = _as_decimal(_value(spend, "reserved_total", Decimal("0.00")))
    except (AmountFormatError, TypeError, ValueError):
        return Decision(DecisionValue.REJECTED, ReasonCode.LIMIT_EXHAUSTED)

    if amount > normalized.max_per_txn:
        return Decision(
            DecisionValue.REJECTED,
            ReasonCode.AMOUNT_EXCEEDS_PER_TXN,
            diff={
                "limit": "max_per_txn",
                "value": str(normalized.max_per_txn),
                "attempted": str(amount),
            },
        )
    if spent + reserved + amount > normalized.total_budget:
        return Decision(
            DecisionValue.REJECTED,
            ReasonCode.BUDGET_EXCEEDED,
            diff={
                "limit": "total_budget",
                "value": str(normalized.total_budget),
                "attempted": str(spent + reserved + amount),
            },
        )
    if normalized.max_txn is not None:
        count = _value(spend, "txn_count_period", 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return Decision(DecisionValue.REJECTED, ReasonCode.LIMIT_EXHAUSTED)
        if count + 1 > normalized.max_txn.count:
            return Decision(
                DecisionValue.REJECTED,
                ReasonCode.LIMIT_EXHAUSTED,
                diff={
                    "limit": "max_txn",
                    "value": normalized.max_txn.count,
                    "attempted": count + 1,
                },
            )
    return Decision(DecisionValue.APPROVED)


def _burst_state(state: BurstState | SpendView | Mapping[str, Any] | None) -> BurstState:
    if isinstance(state, BurstState):
        return state
    if isinstance(state, SpendView):
        return BurstState(
            intents_in_window=state.intents_last_60s,
            cooldown_until=state.cooldown_until,
            open_authorizations=state.open_authorizations,
            escalations_in_hour=state.escalations_last_hour,
        )
    if isinstance(state, Mapping):
        return BurstState(
            intents_in_window=state.get(
                "intents_in_window", state.get("intents_last_60s", state.get("intent_count_60s", 0))
            ),
            cooldown_until=state.get("cooldown_until"),
            open_authorizations=state.get("open_authorizations", state.get("open_authz", 0)),
            escalations_in_hour=state.get(
                "escalations_in_hour", state.get("escalations_last_hour", 0)
            ),
        )
    return BurstState()


def evaluate_burst(
    state: BurstState | SpendView | Mapping[str, Any] | None = None,
    now: datetime | None = None,
    config: BurstConfig | None = None,
    *,
    intents_in_window: int | None = None,
    cooldown_until: datetime | None = None,
    open_authorizations: int | None = None,
    escalations_in_hour: int | None = None,
) -> BurstDecision:
    """Evaluate R-BURST before admitting one candidate intent.

    Counters describe the state *before* the candidate.  Thus three prior
    intents in the 60-second window make the fourth intent the burst trigger.
    A trigger escalates and establishes a ten-minute cooldown; a new intent
    observed while that cooldown is active is rejected.
    """

    if now is None:
        raise ValueError("evaluate_burst requires an explicit now")
    current = ensure_utc(now)
    policy = config or BurstConfig()
    observed = _burst_state(state)
    if intents_in_window is not None:
        observed = BurstState(
            intents_in_window=intents_in_window,
            cooldown_until=observed.cooldown_until,
            open_authorizations=observed.open_authorizations,
            escalations_in_hour=observed.escalations_in_hour,
        )
    if cooldown_until is not None:
        observed = BurstState(
            intents_in_window=observed.intents_in_window,
            cooldown_until=cooldown_until,
            open_authorizations=observed.open_authorizations,
            escalations_in_hour=observed.escalations_in_hour,
        )
    if open_authorizations is not None:
        observed = BurstState(
            intents_in_window=observed.intents_in_window,
            cooldown_until=observed.cooldown_until,
            open_authorizations=open_authorizations,
            escalations_in_hour=observed.escalations_in_hour,
        )
    if escalations_in_hour is not None:
        observed = BurstState(
            intents_in_window=observed.intents_in_window,
            cooldown_until=observed.cooldown_until,
            open_authorizations=observed.open_authorizations,
            escalations_in_hour=escalations_in_hour,
        )

    if observed.cooldown_until is not None and current < observed.cooldown_until:
        return BurstDecision(
            DecisionValue.REJECTED,
            ReasonCode.VELOCITY_BURST,
            cooldown_until=observed.cooldown_until,
            diff={"cause": "cooldown", "until": observed.cooldown_until.isoformat()},
        )

    # More than five escalations in the hour is an explicit deterministic
    # auto-pause rule, not a behavioural score.
    if observed.escalations_in_hour > policy.max_escalations_per_hour:
        return BurstDecision(
            DecisionValue.REJECTED,
            ReasonCode.MANDATE_SUSPENDED,
            auto_suspend=True,
            diff={
                "limit": "escalations_per_hour",
                "value": policy.max_escalations_per_hour,
                "observed": observed.escalations_in_hour,
            },
        )

    if observed.intents_in_window + 1 > policy.max_intents:
        until = current + policy.cooldown
        return BurstDecision(
            DecisionValue.ESCALATED,
            ReasonCode.VELOCITY_BURST,
            cooldown_until=until,
            diff={
                "limit": "intents",
                "window_seconds": int(policy.window.total_seconds()),
                "observed": observed.intents_in_window + 1,
                "cooldown_until": until.isoformat(),
            },
        )

    # The candidate would consume one open authorization slot.  Requiring a
    # human at the cap avoids silently opening a fourth authorization.
    if observed.open_authorizations + 1 > policy.max_open_authorizations:
        return BurstDecision(
            DecisionValue.ESCALATED,
            ReasonCode.VELOCITY_BURST,
            diff={
                "limit": "open_authorizations",
                "value": policy.max_open_authorizations,
                "attempted": observed.open_authorizations + 1,
            },
        )
    return BurstDecision(DecisionValue.APPROVED)


def burst_check(*args: Any, **kwargs: Any) -> BurstDecision:
    return evaluate_burst(*args, **kwargs)


def ttl_for_level(
    level: EscalationLevel | str,
    config: StepUpConfig | None = None,
) -> int:
    policy = config or StepUpConfig()
    normalized = _level(level)
    ttl = policy.ttl_l3_plus if normalized is EscalationLevel.L3_PLUS else policy.ttl_l3
    return int(ttl.total_seconds())


def _level(level: EscalationLevel | str) -> EscalationLevel:
    if isinstance(level, EscalationLevel):
        return level
    if level in {"L3+", "L3_PLUS", "l3+", "l3_plus"}:
        return EscalationLevel.L3_PLUS
    return EscalationLevel(level)


def escalation_deadline(
    created_at: datetime,
    level: EscalationLevel | str,
    config: StepUpConfig | None = None,
) -> datetime:
    return ensure_utc(created_at) + timedelta(seconds=ttl_for_level(level, config))


def escalation_expired(
    timeout_at: datetime,
    now: datetime,
) -> bool:
    return ensure_utc(now) >= ensure_utc(timeout_at)


def evaluate_step_up(
    amount: AmountLike,
    max_per_txn: AmountLike,
    spent_total: AmountLike = Decimal("0.00"),
    reserved_total: AmountLike = Decimal("0.00"),
    total_budget: AmountLike | None = None,
    now: datetime | None = None,
    *,
    config: StepUpConfig | None = None,
    first_escalation: bool = False,
    fresh_agent_key: bool = False,
) -> StepUpDecision:
    """Evaluate the fixed R-STEPUP thresholds.

    The two numeric thresholds are inclusive.  They produce L3+ and require
    UV.  ``first_escalation`` and ``fresh_agent_key`` are corroborative
    high-risk context from decision 0021; they also require L3+ but do not
    invent a new public reason code.
    """

    policy = config or StepUpConfig()
    try:
        candidate = _as_decimal(amount)
        maximum = _as_decimal(max_per_txn)
        spent = _as_decimal(spent_total)
        reserved = _as_decimal(reserved_total)
        budget = None if total_budget is None else _as_decimal(total_budget)
    except AmountFormatError:
        return StepUpDecision(required=True, level=EscalationLevel.L3_PLUS, requires_uv=True)

    reasons: list[ReasonCode] = []
    diff: dict[str, Any] = {}
    if maximum > 0 and candidate >= policy.amount_ratio * maximum:
        reasons.append(ReasonCode.STEPUP_AMOUNT_THRESHOLD)
        diff["amount_threshold"] = {
            "ratio": str(policy.amount_ratio),
            "amount": str(candidate),
            "max_per_txn": str(maximum),
        }
    if budget is not None and budget > 0 and (spent + reserved) / budget >= policy.budget_ratio:
        reasons.append(ReasonCode.STEPUP_BUDGET_USAGE)
        diff["budget_threshold"] = {
            "ratio": str(policy.budget_ratio),
            "spent_plus_reserved": str(spent + reserved),
            "total_budget": str(budget),
        }

    high_risk_context = first_escalation or fresh_agent_key
    if not reasons and not high_risk_context:
        return StepUpDecision(required=False)

    level = EscalationLevel.L3_PLUS if reasons or high_risk_context else EscalationLevel.L3
    ttl_seconds = ttl_for_level(level, policy)
    expires_at = None if now is None else escalation_deadline(now, level, policy)
    if first_escalation:
        diff["first_escalation"] = True
    if fresh_agent_key:
        diff["fresh_agent_key"] = True
    return StepUpDecision(
        required=True,
        level=level,
        reasons=tuple(reasons),
        ttl_seconds=ttl_seconds,
        max_age=ttl_seconds,
        requires_uv=level is EscalationLevel.L3_PLUS,
        expires_at=expires_at,
        diff=diff,
    )


def requires_step_up(
    amount: AmountLike,
    max_per_txn: AmountLike,
    spent_total: AmountLike = Decimal("0.00"),
    reserved_total: AmountLike = Decimal("0.00"),
    total_budget: AmountLike | None = None,
) -> bool:
    return evaluate_step_up(
        amount,
        max_per_txn,
        spent_total,
        reserved_total,
        total_budget,
    ).required


class UVVerifier(Protocol):
    """Local port for the future WebAuthn user-verification adapter."""

    def verify(self, *args: Any, **kwargs: Any) -> bool: ...


class FailClosedUVVerifier:
    """Temporary UV port implementation: every assertion fails closed.

    This is intentionally not a cryptographic verifier.  Until the passkey
    service is wired in, L3+ approval cannot be accepted by the domain layer.
    """

    def verify(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def __call__(self, *args: Any, **kwargs: Any) -> bool:
        return False


UVVerifierStub = FailClosedUVVerifier


def canonical_diff_digest(diff: Mapping[str, Any] | None) -> str:
    """Hash the canonical diff that an eventual UV ceremony must sign."""

    canonical = json.dumps(
        diff or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_escalation(
    escalation: Escalation,
    now: datetime,
    approval: str | bool = "APPROVE",
    *,
    uv_verifier: UVVerifier | None = None,
    assertion: Any = None,
    diff: Mapping[str, Any] | None = None,
    uv_verified: bool | None = None,
) -> Decision:
    """Resolve an escalation without allowing approval to bypass the gate.

    This helper only validates the human-resolution envelope.  The caller
    must re-run ``PolicyGate.evaluate`` before reserving or charging.  L3+
    uses the local UV port and therefore rejects while the port is still the
    fail-closed stub.
    """

    current = ensure_utc(now)
    timeout_at = escalation.timeout_at or escalation_deadline(
        escalation.created_at, escalation.level
    )
    if current >= timeout_at:
        return Decision(DecisionValue.REJECTED, ReasonCode.ESCALATION_TIMEOUT_DENIED)
    if approval is False or str(approval).upper() != "APPROVE":
        return Decision(DecisionValue.REJECTED, ReasonCode.ESCALATION_TIMEOUT_DENIED)

    if _level(escalation.level) is EscalationLevel.L3_PLUS:
        verified = uv_verified
        if verified is None:
            verifier = uv_verifier or FailClosedUVVerifier()
            try:
                verified = bool(
                    verifier.verify(
                        challenge=canonical_diff_digest(
                            diff if diff is not None else escalation.diff
                        ),
                        assertion=assertion,
                        max_age=int((timeout_at - escalation.created_at).total_seconds()),
                    )
                )
            except Exception:
                verified = False
        if not verified:
            return Decision(DecisionValue.REJECTED, ReasonCode.ESCALATION_TIMEOUT_DENIED)
    return Decision(DecisionValue.APPROVED)


class PolicyGate:
    """The pure gate implementing mandate, scope, limits, burst, and step-up."""

    def __init__(
        self,
        *,
        burst_config: BurstConfig | None = None,
        stepup_config: StepUpConfig | None = None,
    ) -> None:
        self.burst_config = burst_config or BurstConfig()
        self.stepup_config = stepup_config or StepUpConfig()

    def evaluate(
        self,
        mandate: MandateClaims | Mapping[str, Any],
        intent: PurchaseIntent | Mapping[str, Any],
        spend: SpendView | Mapping[str, Any],
        now: datetime,
        offer: Offer | Mapping[str, Any] | None = None,
        *,
        approved_stepup: bool = False,
    ) -> Decision:
        """Evaluate one candidate; ``offer`` is optional for port compatibility.

        If an offer is supplied, R-PRICE and scope/category checks are made
        here.  If it is omitted, an intent's optional ``offer_amount`` still
        enables the price check; the merchant must pass the catalogue offer
        for a complete decision.
        """

        try:
            current = ensure_utc(now)
        except ValueError:
            return Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED)

        state_reason = mandate_state_reason(mandate, current)
        if state_reason is not None:
            return Decision(DecisionValue.REJECTED, state_reason)

        mandate_jti = _value(mandate, "jti")
        intent_mandate_jti = _value(intent, "mandate_jti")
        if not mandate_jti or intent_mandate_jti != mandate_jti:
            return Decision(DecisionValue.REJECTED, ReasonCode.INVALID_PROOF_OF_POSSESSION)

        mandate_agent = _value(mandate, "agent", _value(mandate, "agent_id"))
        intent_agent = _value(intent, "agent", _value(intent, "agent_id"))
        if not mandate_agent or intent_agent != mandate_agent:
            return Decision(DecisionValue.REJECTED, ReasonCode.INVALID_PROOF_OF_POSSESSION)

        mandate_currency = str(_value(mandate, "currency", "")).upper()
        intent_currency = str(_value(intent, "currency", "")).upper()
        if not mandate_currency or intent_currency != mandate_currency:
            return Decision(DecisionValue.REJECTED, ReasonCode.CONDITION_FAILED)

        offer_mapping = _as_offer_mapping(offer)
        if offer_mapping is not None:
            if _value(intent, "offer_id") != offer_mapping.get("offer_id"):
                return Decision(DecisionValue.REJECTED, ReasonCode.CONDITION_FAILED)
            if not price_matches(_value(intent, "amount"), offer_mapping.get("amount")):
                return price_check(_value(intent, "amount"), offer_mapping.get("amount"))
            offer_merchant = str(offer_mapping.get("merchant_id", ""))
            offer_category = str(offer_mapping.get("category", ""))
            if _value(intent, "merchant_id") != offer_merchant:
                return Decision(DecisionValue.REJECTED, ReasonCode.MERCHANT_NOT_ALLOWED)
        else:
            intent_offer_amount = _value(intent, "offer_amount")
            if intent_offer_amount is not None and not price_matches(
                _value(intent, "amount"), intent_offer_amount
            ):
                return price_check(_value(intent, "amount"), intent_offer_amount)
            offer_merchant = str(_value(intent, "merchant_id", ""))
            offer_category = str(_value(intent, "category", ""))

        scope = _value(mandate, "scope")
        try:
            normalized_scope = _as_scope(scope)
        except (TypeError, ValueError):
            return Decision(DecisionValue.REJECTED, ReasonCode.MERCHANT_NOT_ALLOWED)
        if not scope_allows(normalized_scope, offer_merchant, offer_category):
            if offer_merchant not in normalized_scope.merchants:
                return Decision(DecisionValue.REJECTED, ReasonCode.MERCHANT_NOT_ALLOWED)
            return Decision(DecisionValue.REJECTED, ReasonCode.CATEGORY_FORBIDDEN)

        conditions = _value(mandate, "conditions")
        if conditions and not evaluate_conditions(conditions, offer_mapping, current):
            return Decision(DecisionValue.REJECTED, ReasonCode.CONDITION_FAILED)

        limit_decision = _limits_decision(
            _value(mandate, "limits"), _value(intent, "amount"), spend
        )
        if limit_decision.is_rejected:
            return limit_decision

        burst = evaluate_burst(
            spend,
            current,
            self.burst_config,
        )
        if burst.decision is DecisionValue.REJECTED:
            return Decision(
                DecisionValue.REJECTED,
                burst.reason_code,
                diff=burst.diff,
            )

        amount = _value(intent, "amount")
        limits = _value(mandate, "limits")
        max_per_txn = _value(limits, "max_per_txn") if limits is not None else None
        total_budget = _value(limits, "total_budget") if limits is not None else None
        stepup = (
            StepUpDecision(required=False)
            if approved_stepup
            else evaluate_step_up(
                amount,
                max_per_txn,
                _value(spend, "spent_total", Decimal("0.00")),
                _value(spend, "reserved_total", Decimal("0.00")),
                total_budget,
                current,
                config=self.stepup_config,
            )
        )

        escalations: list[Decision] = []
        if burst.decision is DecisionValue.ESCALATED:
            escalations.append(
                Decision(
                    DecisionValue.ESCALATED,
                    burst.reason_code,
                    diff=burst.diff,
                    level=EscalationLevel.L3,
                    ttl_seconds=ttl_for_level(EscalationLevel.L3, self.stepup_config),
                )
            )
        if stepup.required:
            reason = stepup.reasons[0] if stepup.reasons else None
            escalations.append(
                Decision(
                    DecisionValue.ESCALATED,
                    reason,
                    diff=stepup.diff,
                    level=stepup.level,
                    ttl_seconds=stepup.ttl_seconds,
                    requires_uv=stepup.requires_uv,
                )
            )
        if not escalations:
            return Decision(DecisionValue.APPROVED)

        # A high-risk step-up takes precedence over an ordinary L3 signal.
        selected = max(
            escalations,
            key=lambda item: 1 if item.level is EscalationLevel.L3_PLUS else 0,
        )
        return selected


__all__ = [
    "VERDICTIVE_REASONS",
    "CORROBORATIVE_REASONS",
    "RiskSignal",
    "UVVerifier",
    "FailClosedUVVerifier",
    "UVVerifierStub",
    "PolicyGate",
    "price_matches",
    "compare_price",
    "price_check",
    "scope_allows",
    "mandate_state_reason",
    "mandate_is_active",
    "evaluate_conditions",
    "evaluate_burst",
    "burst_check",
    "evaluate_step_up",
    "requires_step_up",
    "ttl_for_level",
    "escalation_deadline",
    "escalation_expired",
    "resolve_escalation",
    "canonical_diff_digest",
    "is_verdictive",
    "is_corroborative",
    "decision_from_signals",
]
