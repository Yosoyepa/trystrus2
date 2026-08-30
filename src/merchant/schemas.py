"""Merchant DTOs — mirrors the v1.1 additions in contracts/api.yaml."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, field_validator

from trustlib.models import Offer, PurchaseIntent, Receipt


def _fixed_amount(value: str) -> str:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("amount must be a decimal string") from exc
    if amount <= 0 or amount.as_tuple().exponent != -2:
        raise ValueError("amount must be positive with exactly two decimal places")
    return f"{amount:.2f}"


class PriceUpdate(BaseModel):
    amount: str

    _validate_amount = field_validator("amount")(_fixed_amount)

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)


class CheckoutQuoteRequest(BaseModel):
    offer_id: str


class CheckoutQuote(BaseModel):
    order_id: str
    offer: Offer
    checkout_jwt: str
    checkout_hash: str


class ChargeRequest(BaseModel):
    purchase_id: str
    mandate_id: str
    mandate_sd_jwt: str
    intent: PurchaseIntent
    intent_jwt: str
    checkout_jwt: str
    payment_method_ref: str
    amount: str
    currency: str = "USD"
    idempotency_key: str | None = None

    _validate_amount = field_validator("amount")(_fixed_amount)

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)


class ChargeResult(Receipt):
    """Named locally so OpenAPI and the rail model stay visibly aligned."""


class PurchaseSubmission(BaseModel):
    status: str = "submitted"
    purchase_id: str
