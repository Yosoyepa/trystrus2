"""Pure domain value objects used by Aval's deterministic decision layer.

The API and persistence layers are deliberately not imported here.  These
objects are small enough to be used by the kernel, the merchant adapter, and
unit tests without bringing an application framework into the enforcement
path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping, Sequence


class AmountFormatError(ValueError):
    """Raised when a monetary value is not a non-negative two-decimal amount."""


_AMOUNT_RE = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{2}\Z")
_CENT = Decimal("0.01")


AmountLike = str | Decimal | int


def _decimal_amount(value: AmountLike, *, require_canonical_string: bool = False) -> Decimal:
    """Convert a money-like value without ever going through ``float``.

    Strings represent wire values and therefore must use exactly two decimal
    places when ``require_canonical_string`` is true.  Decimal and integer
    values are accepted for values read from a NUMERIC database column or for
    mandate limits, but values with sub-cent precision are rejected instead of
    rounded.
    """

    if isinstance(value, bool) or isinstance(value, float):
        raise AmountFormatError("money must be a string, Decimal, or integer")

    if isinstance(value, str):
        if require_canonical_string and _AMOUNT_RE.fullmatch(value) is None:
            raise AmountFormatError("money strings must contain exactly two decimals")
        if not require_canonical_string and value == "":
            raise AmountFormatError("money cannot be empty")
        raw = value
    elif isinstance(value, (Decimal, int)):
        raw = str(value)
    else:
        raise AmountFormatError("unsupported money type")

    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise AmountFormatError("invalid decimal amount") from None

    if not amount.is_finite() or amount < 0:
        raise AmountFormatError("amount must be finite and non-negative")

    try:
        quantized = amount.quantize(_CENT)
    except InvalidOperation:
        raise AmountFormatError("amount cannot be represented to two decimals") from None

    if quantized != amount:
        raise AmountFormatError("amount has more than two decimal places")
    return quantized


def canonical_amount(value: AmountLike) -> str:
    """Return the canonical wire representation of a two-decimal amount."""

    if isinstance(value, str):
        amount = _decimal_amount(value, require_canonical_string=True)
    else:
        amount = _decimal_amount(value)
    return format(amount, ".2f")


def amount_decimal(value: AmountLike) -> Decimal:
    """Return a validated, two-decimal ``Decimal`` without float coercion."""

    return _decimal_amount(value, require_canonical_string=isinstance(value, str))


def ensure_utc(value: datetime) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("domain timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class DecisionValue(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class ReasonCode(str, Enum):
    """Reason codes frozen by ``contracts/schemas.md`` v1.1."""

    AMOUNT_EXCEEDS_PER_TXN = "AMOUNT_EXCEEDS_PER_TXN"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    LIMIT_EXHAUSTED = "LIMIT_EXHAUSTED"
    CATEGORY_FORBIDDEN = "CATEGORY_FORBIDDEN"
    MERCHANT_NOT_ALLOWED = "MERCHANT_NOT_ALLOWED"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_SUSPENDED = "MANDATE_SUSPENDED"
    MANDATE_EXHAUSTED = "MANDATE_EXHAUSTED"
    CONDITION_FAILED = "CONDITION_FAILED"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    INVALID_PROOF_OF_POSSESSION = "INVALID_PROOF_OF_POSSESSION"
    DUPLICATE_JTI = "DUPLICATE_JTI"
    NONCE_REUSED = "NONCE_REUSED"
    ESCALATION_TIMEOUT_DENIED = "ESCALATION_TIMEOUT_DENIED"
    RAIL_ERROR = "RAIL_ERROR"
    RAIL_TOKEN_DELETED = "RAIL_TOKEN_DELETED"
    VELOCITY_BURST = "VELOCITY_BURST"
    STEPUP_AMOUNT_THRESHOLD = "STEPUP_AMOUNT_THRESHOLD"
    STEPUP_BUDGET_USAGE = "STEPUP_BUDGET_USAGE"
    PRICE_MISMATCH_AUTO_REFUND = "PRICE_MISMATCH_AUTO_REFUND"
    WEBHOOK_INVALID = "WEBHOOK_INVALID"


class MandateStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"


class EscalationLevel(str, Enum):
    L3 = "L3"
    L3_PLUS = "L3+"


def _as_mandate_status(value: MandateStatus | str) -> MandateStatus | None:
    try:
        return value if isinstance(value, MandateStatus) else MandateStatus(value)
    except (TypeError, ValueError):
        return None


def _as_level(value: EscalationLevel | str) -> EscalationLevel:
    if isinstance(value, EscalationLevel):
        return value
    if value in {"L3+", "L3_PLUS", "l3+", "l3_plus"}:
        return EscalationLevel.L3_PLUS
    return EscalationLevel(value)


def _limit_decimal(value: AmountLike) -> Decimal:
    """Normalize a mandate limit while allowing integer JSON numbers."""

    return _decimal_amount(value)


@dataclass(frozen=True, slots=True)
class MaxTxnLimit:
    count: int
    period: str

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise ValueError("max_txn.count must be a non-negative integer")
        if self.period not in {"day", "week", "month"}:
            raise ValueError("max_txn.period must be day, week, or month")


@dataclass(frozen=True, slots=True)
class MandateLimits:
    max_per_txn: AmountLike
    total_budget: AmountLike
    max_txn: MaxTxnLimit | Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_per_txn", _limit_decimal(self.max_per_txn))
        object.__setattr__(self, "total_budget", _limit_decimal(self.total_budget))
        if isinstance(self.max_txn, Mapping):
            object.__setattr__(self, "max_txn", MaxTxnLimit(**self.max_txn))


@dataclass(frozen=True, slots=True)
class MandateScope:
    categories: Sequence[str] = field(default_factory=tuple)
    merchants: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        categories = tuple(str(item) for item in self.categories)
        merchants = tuple(str(item) for item in self.merchants)
        if any(not item for item in categories + merchants):
            raise ValueError("scope entries cannot be empty")
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "merchants", merchants)


@dataclass(frozen=True, slots=True)
class MandateValidity:
    not_before: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        not_before = ensure_utc(self.not_before)
        expires_at = ensure_utc(self.expires_at)
        if expires_at <= not_before:
            raise ValueError("mandate validity must have a positive duration")
        object.__setattr__(self, "not_before", not_before)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class MandateClaims:
    """The enforcement-relevant subset of the signed MandateClaims object."""

    jti: str
    agent: str | None = None
    currency: str = ""
    scope: MandateScope | Mapping[str, Any] = field(default_factory=MandateScope)
    limits: MandateLimits | Mapping[str, Any] | None = None
    validity: MandateValidity | Mapping[str, Any] | None = None
    status: MandateStatus | str = MandateStatus.ACTIVE
    conditions: Mapping[str, Any] | None = None
    payment_method_ref: str | None = None
    subject: str | None = None
    agent_id: str | None = None
    parent_jti: str | None = None

    def __post_init__(self) -> None:
        if not self.jti:
            raise ValueError("mandate jti is required")
        agent = self.agent or self.agent_id
        if agent is not None and not agent:
            raise ValueError("agent cannot be empty")
        object.__setattr__(self, "agent", agent)
        object.__setattr__(self, "currency", self.currency.upper())
        if isinstance(self.scope, Mapping):
            object.__setattr__(self, "scope", MandateScope(**self.scope))
        if isinstance(self.limits, Mapping):
            object.__setattr__(self, "limits", MandateLimits(**self.limits))
        if isinstance(self.validity, Mapping):
            object.__setattr__(self, "validity", MandateValidity(**self.validity))
        status = _as_mandate_status(self.status)
        if status is None:
            # Unknown states are intentionally retained for the gate to deny
            # fail-closed rather than being silently converted to active.
            object.__setattr__(self, "status", str(self.status))
        else:
            object.__setattr__(self, "status", status)


@dataclass(frozen=True, slots=True)
class Offer:
    offer_id: str
    merchant_id: str
    category: str
    amount: AmountLike
    currency: str
    title: str = ""
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", canonical_amount(self.amount))
        object.__setattr__(self, "currency", self.currency.upper())


@dataclass(frozen=True, slots=True)
class PurchaseIntent:
    """The signed intent fields consumed by the policy gate."""

    jti: str
    mandate_jti: str
    agent: str
    merchant_id: str
    offer_id: str
    amount: AmountLike
    currency: str
    nonce: str = ""
    iat: int | datetime | None = None
    exp: int | datetime | None = None
    category: str | None = None
    offer_amount: AmountLike | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.jti or not self.mandate_jti:
            raise ValueError("intent and mandate JTIs are required")
        if self.agent_id and self.agent and self.agent_id != self.agent:
            raise ValueError("agent and agent_id disagree")
        object.__setattr__(self, "agent_id", self.agent_id or self.agent)
        object.__setattr__(self, "amount", canonical_amount(self.amount))
        if self.offer_amount is not None:
            object.__setattr__(self, "offer_amount", canonical_amount(self.offer_amount))
        object.__setattr__(self, "currency", self.currency.upper())


@dataclass(frozen=True, slots=True)
class SpendView:
    """Read-only ledger projection supplied to the pure policy gate."""

    spent_total: AmountLike = Decimal("0.00")
    reserved_total: AmountLike = Decimal("0.00")
    txn_count_period: int = 0
    mandate_status: MandateStatus | str = MandateStatus.ACTIVE
    intents_last_60s: int = 0
    escalations_last_hour: int = 0
    open_authorizations: int = 0
    cooldown_until: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "spent_total", _limit_decimal(self.spent_total))
        object.__setattr__(self, "reserved_total", _limit_decimal(self.reserved_total))
        for field_name in (
            "txn_count_period",
            "intents_last_60s",
            "escalations_last_hour",
            "open_authorizations",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.cooldown_until is not None:
            object.__setattr__(self, "cooldown_until", ensure_utc(self.cooldown_until))

    @property
    def intent_count_60s(self) -> int:
        """Compatibility spelling used by some callers of the policy port."""

        return self.intents_last_60s

    @property
    def escalation_count_hour(self) -> int:
        return self.escalations_last_hour

    @property
    def open_authz(self) -> int:
        return self.open_authorizations


@dataclass(frozen=True, slots=True)
class Decision:
    decision: DecisionValue | str
    reason_code: ReasonCode | str | None = None
    reservation_id: str | None = None
    diff: Mapping[str, Any] | None = None
    level: EscalationLevel | str | None = None
    ttl_seconds: int | None = None
    requires_uv: bool = False

    def __post_init__(self) -> None:
        try:
            decision = self.decision if isinstance(self.decision, DecisionValue) else DecisionValue(self.decision)
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown decision") from exc
        object.__setattr__(self, "decision", decision)
        if self.reason_code is not None:
            try:
                reason = self.reason_code if isinstance(self.reason_code, ReasonCode) else ReasonCode(self.reason_code)
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown reason code") from exc
            object.__setattr__(self, "reason_code", reason)
        if self.level is not None:
            object.__setattr__(self, "level", _as_level(self.level))
        if self.ttl_seconds is not None and self.ttl_seconds < 0:
            raise ValueError("TTL cannot be negative")

    @property
    def is_approved(self) -> bool:
        return self.decision is DecisionValue.APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.decision is DecisionValue.REJECTED

    @property
    def is_escalated(self) -> bool:
        return self.decision is DecisionValue.ESCALATED


@dataclass(frozen=True, slots=True)
class BurstConfig:
    max_intents: int = 3
    window: timedelta = timedelta(seconds=60)
    cooldown: timedelta = timedelta(minutes=10)
    max_open_authorizations: int = 3
    max_escalations_per_hour: int = 5

    def __post_init__(self) -> None:
        if self.max_intents < 0 or self.max_open_authorizations < 0 or self.max_escalations_per_hour < 0:
            raise ValueError("burst limits cannot be negative")
        if self.window <= timedelta(0) or self.cooldown <= timedelta(0):
            raise ValueError("burst durations must be positive")


@dataclass(frozen=True, slots=True)
class BurstState:
    """Counters observed before the candidate intent is admitted."""

    intents_in_window: int = 0
    cooldown_until: datetime | None = None
    open_authorizations: int = 0
    escalations_in_hour: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "intents_in_window",
            "open_authorizations",
            "escalations_in_hour",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.cooldown_until is not None:
            object.__setattr__(self, "cooldown_until", ensure_utc(self.cooldown_until))


@dataclass(frozen=True, slots=True)
class BurstDecision:
    decision: DecisionValue
    reason_code: ReasonCode | None = None
    cooldown_until: datetime | None = None
    auto_suspend: bool = False
    diff: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class StepUpConfig:
    amount_ratio: Decimal = Decimal("0.70")
    budget_ratio: Decimal = Decimal("0.80")
    ttl_l3: timedelta = timedelta(seconds=120)
    ttl_l3_plus: timedelta = timedelta(seconds=300)

    def __post_init__(self) -> None:
        for field_name in ("amount_ratio", "budget_ratio"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or value <= 0 or value > 1:
                raise ValueError(f"{field_name} must be a Decimal in (0, 1]")
        if self.ttl_l3 <= timedelta(0) or self.ttl_l3_plus <= timedelta(0):
            raise ValueError("step-up TTLs must be positive")


@dataclass(frozen=True, slots=True)
class StepUpDecision:
    required: bool
    level: EscalationLevel | None = None
    reasons: tuple[ReasonCode, ...] = ()
    ttl_seconds: int | None = None
    max_age: int | None = None
    requires_uv: bool = False
    expires_at: datetime | None = None
    diff: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Escalation:
    """Pure representation of an escalation deadline and its risk level."""

    level: EscalationLevel | str
    created_at: datetime
    timeout_at: datetime | None = None
    diff: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", _as_level(self.level))
        created_at = ensure_utc(self.created_at)
        object.__setattr__(self, "created_at", created_at)
        if self.timeout_at is not None:
            timeout_at = ensure_utc(self.timeout_at)
            if timeout_at <= created_at:
                raise ValueError("escalation timeout must be after creation")
            object.__setattr__(self, "timeout_at", timeout_at)


# Friendly aliases matching the vocabulary used by the contracts and plan.
MaxTxn = MaxTxnLimit
MandateStatusValue = MandateStatus
Level = EscalationLevel


__all__ = [
    "AmountFormatError",
    "AmountLike",
    "Decision",
    "DecisionValue",
    "ReasonCode",
    "MandateStatus",
    "EscalationLevel",
    "MaxTxnLimit",
    "MaxTxn",
    "MandateLimits",
    "MandateScope",
    "MandateValidity",
    "MandateClaims",
    "Offer",
    "PurchaseIntent",
    "SpendView",
    "BurstConfig",
    "BurstState",
    "BurstDecision",
    "StepUpConfig",
    "StepUpDecision",
    "Escalation",
    "canonical_amount",
    "amount_decimal",
    "ensure_utc",
]
