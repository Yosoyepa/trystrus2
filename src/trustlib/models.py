"""Pydantic models for the frozen contracts.

Source of truth: `aval/contracts/schemas.md` §1-3 and `aval/contracts/api.yaml`.
If this file and a contract disagree, **the contract wins** (AGENTS.md).

Community property: Dev 1, 2 and 3 all import these. Changing a field here is
a contract change -- PR with a decision record, per PLAN-PARALELO §6.2.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ==========================================================================
# Enums
# ==========================================================================
class ReasonCode(StrEnum):
    """The 18 codes of api.yaml#/components/schemas/ReasonCode.

    Semantics and producer for each: schemas.md §7. These strings reach the
    UI verbatim, so all three consoles say the same thing about the same
    refusal.
    """

    # limits (producer: gate)
    AMOUNT_EXCEEDS_PER_TXN = "AMOUNT_EXCEEDS_PER_TXN"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    LIMIT_EXHAUSTED = "LIMIT_EXHAUSTED"
    # scope (producer: gate)
    CATEGORY_FORBIDDEN = "CATEGORY_FORBIDDEN"
    MERCHANT_NOT_ALLOWED = "MERCHANT_NOT_ALLOWED"
    # mandate state (producer: verify)
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    MANDATE_SUSPENDED = "MANDATE_SUSPENDED"
    MANDATE_EXHAUSTED = "MANDATE_EXHAUSTED"
    # conditions (producer: gate)
    CONDITION_FAILED = "CONDITION_FAILED"
    # crypto (producer: verify) -- possible impersonation
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    INVALID_PROOF_OF_POSSESSION = "INVALID_PROOF_OF_POSSESSION"
    # replay (producer: verify)
    DUPLICATE_JTI = "DUPLICATE_JTI"
    NONCE_REUSED = "NONCE_REUSED"
    # human in the loop
    ESCALATION_TIMEOUT_DENIED = "ESCALATION_TIMEOUT_DENIED"
    # rail
    RAIL_ERROR = "RAIL_ERROR"
    RAIL_TOKEN_DELETED = "RAIL_TOKEN_DELETED"


class MandateStatus(StrEnum):
    """State machine of PLAN.md §7.

    draft -> active -> (suspended <-> active) -> {revoked | expired | exhausted}
    The last three are terminal.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"

    @property
    def is_terminal(self) -> bool:
        return self in (MandateStatus.REVOKED, MandateStatus.EXPIRED,
                        MandateStatus.EXHAUSTED)


class DecisionOutcome(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"


class PurchaseStatusValue(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    AWAITING_ESCALATION = "awaiting_escalation"
    CHARGING = "charging"
    CAPTURED = "captured"
    REJECTED = "rejected"
    COMPENSATED = "compensated"


class EscalationStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class Period(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


# ==========================================================================
# Mandate (schemas.md §1)
# ==========================================================================
class MaxTxn(BaseModel):
    count: int
    period: Period


class MandateLimits(BaseModel):
    max_per_txn: Decimal | None = None
    total_budget: Decimal | None = None
    max_txn: MaxTxn | None = None


class MandateScope(BaseModel):
    categories: list[str] = Field(default_factory=list)
    merchants: list[str] = Field(default_factory=list)


class MandateValidity(BaseModel):
    not_before: datetime
    expires_at: datetime


class ConfirmationKey(BaseModel):
    """RFC 7800 `cnf` -- the agent's public key, bound into the mandate.

    This is what makes impersonation fail at the signature: a cloned agent
    without the private half cannot produce a valid intent (decision #9).
    AP2 requires exactly this claim on open mandates.
    """

    jwk: dict[str, Any]


class MandateClaimsInput(BaseModel):
    """What a caller supplies to create a mandate (api.yaml MandateCreate)."""

    user_id: str
    agent_id: str
    currency: str = "USD"
    scope: MandateScope
    limits: MandateLimits
    validity: MandateValidity
    conditions: dict[str, Any] | None = None
    payment_method_ref: str | None = None
    agent_jwk: dict[str, Any] | None = None


class MandateClaims(BaseModel):
    """The signed SD-JWT payload (schemas.md §1).

    `conditions` is pure JsonLogic evaluated over `{"offer": Offer, "now": iso}`
    -- variables limited to `offer.*` and `now`, no custom functions.

    `payment_method_ref` is OPAQUE: whoever receives it does not interpret it,
    only hands it to the PaymentRail.
    """

    model_config = ConfigDict(populate_by_name=True)

    iss: str
    iat: int
    nbf: int
    exp: int
    jti: str
    type: str = "purchase_mandate_v1"
    sub: str
    agent: str
    cnf: ConfirmationKey
    payment_method_ref: str | None = None
    currency: str = "USD"
    scope: MandateScope
    conditions: dict[str, Any] | None = None
    limits: MandateLimits
    validity: MandateValidity
    parent_jti: str | None = None

    # --- AP2 projection (additive; native fields above are unchanged) ------
    # Our mandate *is* an AP2 Open Payment Mandate. Carrying `vct` and
    # `constraints` makes that machine-checkable instead of merely claimed.
    # See docs/decisions/0023 and trustlib/ap2.py.
    vct: str | None = None
    constraints: list[dict[str, Any]] | None = None


class IssuedMandate(BaseModel):
    sd_jwt: str
    jti: str
    claims: MandateClaims


# ==========================================================================
# Purchase intent (schemas.md §2)
# ==========================================================================
class PurchaseIntent(BaseModel):
    """Signed by the agent with the `cnf.jwk` key, over canonical JSON (JCS).

    `amount` is a fixed 2-decimal string on purpose -- it avoids float drift
    between services. It MUST equal the referenced offer's price; the verify
    endpoint checks that against the catalog, which removes price manipulation
    at the source (schemas.md §2).

    `exp - iat <= 120s`; `jti` and `nonce` globally unique.
    """

    typ: str = "purchase_intent_v1"
    mandate_jti: str
    agent: str
    merchant_id: str
    offer_id: str
    amount: str
    currency: str = "USD"
    nonce: str
    jti: str
    iat: int
    exp: int

    # AP2 binding to the merchant's signed Checkout JWT. Optional by design:
    # making it required would break schemas.md §2, a contract frozen for
    # Dev 1 and Dev 2 (proposed as additive v1.1 in decision 0023).
    checkout_hash: str | None = None

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)


# ==========================================================================
# Offers (schemas.md §6, api.yaml Offer)
# ==========================================================================
class Offer(BaseModel):
    offer_id: str
    merchant_id: str
    category: str
    title: str
    amount: str
    currency: str = "USD"
    origin: str | None = None
    destination: str | None = None
    date: str | None = None
    description: str | None = None

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)


# ==========================================================================
# Decision / spend (schemas.md §3)
# ==========================================================================
class Decision(BaseModel):
    decision: DecisionOutcome
    reason_code: ReasonCode | None = None
    reservation_id: str | None = None  # APPROVED only; TTL 120 s
    diff: dict[str, Any] | None = None  # ESCALATED only
    expires_in: int | None = None


class SpendView(BaseModel):
    """Ledger projection the gate evaluates limits against."""

    spent_total: Decimal
    reserved_total: Decimal
    txn_count_period: int
    mandate_status: MandateStatus


# ==========================================================================
# Rail (schemas.md §3 -- PaymentRail return types)
# ==========================================================================
class SetupToken(BaseModel):
    setup_token_id: str
    approve_url: str
    expires_at: datetime | None = None
    simulated: bool = False


class Receipt(BaseModel):
    purchase_id: str
    capture_id: str
    amount: str
    currency: str
    captured_at: datetime
    mandate_jti: str
    simulated: bool = False


class DisputeRef(BaseModel):
    dispute_id: str
    capture_id: str
    reason: str = "UNAUTHORISED"
    status: str = "open"
    simulated: bool = False


class WebhookEvent(BaseModel):
    event_id: str
    type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: datetime


# ==========================================================================
# Escalations (schemas.md §5, api.yaml Escalation)
# ==========================================================================
class EscalationResolution(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    approver: str
    channel: Literal["telegram", "web"]
    resolved_at: datetime
    receipt_sig: str | None = None


class Escalation(BaseModel):
    escalation_id: str
    mandate_id: str
    purchase_id: str
    status: EscalationStatus
    diff: dict[str, Any] | None = None
    timeout_at: datetime
    resolution: EscalationResolution | None = None


# ==========================================================================
# Events (schemas.md §4)
# ==========================================================================
class EventEnvelope(BaseModel):
    """Single envelope for outbox -> SSE / bot / merchant webhook."""

    event_id: str
    type: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: datetime


class JWKSet(BaseModel):
    keys: list[dict[str, Any]]


class Rejection(BaseModel):
    reason_code: ReasonCode
    message: str | None = None
    purchase_id: str | None = None
