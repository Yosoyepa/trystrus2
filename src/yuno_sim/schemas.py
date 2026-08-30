"""DTOs for the orchestrator's REST surface.

Shaped like a payment orchestrator's API rather than like our internals, since
the point of decision 0024 is to propose what such a surface could look like.
Maps one-to-one onto `trustlib.AsyncPaymentRail`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EnrollRequest(BaseModel):
    mandate_id: str


class SetupTokenView(BaseModel):
    setup_token_id: str
    approve_url: str
    expires_at: datetime
    simulated: bool = True


class PaymentTokenView(BaseModel):
    token_id: str
    mandate_id: str
    instrument_label: str | None = None
    simulated: bool = True


class CaptureRequest(BaseModel):
    token_id: str
    amount: str = Field(description="Decimal string, 2dp — never a float")
    currency: str = "USD"
    intent_ref: str
    purchase_id: str | None = None

    # The two fields that make this rail different. Optional at the type
    # level so the frozen PaymentRail signature still fits, but a capture
    # without `mandate_sd_jwt` is refused — this rail does not charge on a
    # merchant's word alone (see ap2_verifier).
    mandate_sd_jwt: str | None = Field(
        default=None, description="The AP2 Payment Mandate being exercised.")
    checkout_jwt: str | None = Field(
        default=None,
        description="The merchant's ES256 Checkout JWT. When present, the "
                    "charged amount must match the total it commits to.")


class ReceiptView(BaseModel):
    purchase_id: str
    capture_id: str
    amount: str
    currency: str
    captured_at: datetime
    mandate_jti: str
    simulated: bool = True


class DisputeRequest(BaseModel):
    reason: str = "UNAUTHORISED"


class DisputeView(BaseModel):
    dispute_id: str
    capture_id: str
    reason: str
    status: str
    outcome: str | None = None
    findings: list[str] = Field(default_factory=list)
    simulated: bool = True
