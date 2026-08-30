"""Request/response DTOs for the kernel's identity endpoints.

Mirrors `aval/contracts/api.yaml`. Where this file and the OpenAPI disagree,
**the contract wins** (AGENTS.md) — Dev 4 generates TypeScript from the yaml,
so a drift here becomes a frontend that cannot talk to us.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from trustlib.models import (
    Escalation,
    MandateLimits,
    MandateScope,
    MandateValidity,
    ReasonCode,
)


# ==========================================================================
# Create
# ==========================================================================
class MandateCreate(BaseModel):
    """`POST /mandates` — api.yaml MandateCreate."""

    user_id: str = Field(examples=["usr_marta"])
    agent_id: str = Field(examples=["agt_flights"])
    currency: str = "USD"
    scope: MandateScope
    limits: MandateLimits
    validity: MandateValidity
    conditions: dict[str, Any] | None = Field(
        default=None,
        description='JsonLogic over the offer, e.g. {"<": [{"var": "offer.price"}, 150]}',
    )
    agent_jwk: dict[str, Any] | None = Field(
        default=None,
        description="The agent's public key. Bound as cnf.jwk — this is what "
        "makes an impersonated agent fail at the signature.",
    )
    payment_method_ref: str | None = None
    email: str | None = Field(
        default=None, description="Selectively disclosable; withheld by default."
    )
    shipping_address: str | None = Field(
        default=None, description="Selectively disclosable; withheld by default."
    )


class PaymentEnroll(BaseModel):
    approve_url: str
    setup_token_id: str
    simulated: bool = True


class MandateDraft(BaseModel):
    """201 from `POST /mandates`: nothing is signed yet."""

    mandate_id: str
    status: Literal["draft"] = "draft"
    jti: str
    passkey_challenge: dict[str, Any] = Field(
        description="WebAuthn options. The challenge IS the mandate's "
        "canonical hash, so the gesture signs these exact terms."
    )
    payment_enroll: PaymentEnroll | None = None


# ==========================================================================
# Activate / revoke
# ==========================================================================
class PasskeyAssertion(BaseModel):
    """WebAuthn AuthenticationResponse, passed through to the verifier."""

    model_config = {"extra": "allow"}

    id: str
    rawId: str | None = None
    response: dict[str, Any]
    type: str = "public-key"
    clientExtensionResults: dict[str, Any] = Field(default_factory=dict)


class MandateActive(BaseModel):
    """200 from `passkey/assert`: now it exists in signed form."""

    mandate_id: str
    status: Literal["active"] = "active"
    sd_jwt: str
    jti: str


class MandateView(BaseModel):
    """What the consoles render. Never the raw SD-JWT (api.yaml)."""

    mandate_id: str
    status: str
    jti: str
    limits: MandateLimits
    scope: MandateScope
    spent: Decimal
    reserved: Decimal
    txn_count_period: int
    payment_method_ref: str | None = None
    parent_jti: str | None = None
    created_at: datetime


class Rejection(BaseModel):
    reason_code: ReasonCode
    message: str | None = None
    purchase_id: str | None = None


# ==========================================================================
# Escalations — the human may authorize a retry, never bypass the gate
# ==========================================================================
class ResolveRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    approver: str
    channel: Literal["telegram", "web"]
    # The frozen API exposes sticky approvals.  The first M3 implementation
    # deliberately handles non-sticky resolutions; it refuses sticky rather
    # than granting a broader mandate without returning the derived SD-JWT.
    sticky: dict[str, Any] | None = None


EscalationView = Escalation


# ==========================================================================
# Passkey registration (prerequisite for everything above)
# ==========================================================================
class RegistrationBegin(BaseModel):
    user_id: str
    user_name: str | None = None


class RegistrationOptions(BaseModel):
    options: dict[str, Any]
    challenge: str


class RegistrationComplete(BaseModel):
    model_config = {"extra": "allow"}

    user_id: str
    challenge: str
    credential: dict[str, Any]


class CredentialView(BaseModel):
    credential_id: str
    user_id: str
    sign_count: int
