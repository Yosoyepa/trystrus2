"""Deterministic generators -- what makes four independent builds possible.

Every workstream tests against these instead of against another dev's running
service (PLAN-PARALELO §4). Seeded, so a failing test reproduces exactly.

The canonical scenario mirrors schemas.md §9: Marta lets an agent buy flights
from VuelaYa under $150, at most 3 times a month, USD 400 total.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from jwcrypto import jwk

from . import ids
from .jose import generate_ed25519, public_jwk
from .models import (
    ConfirmationKey,
    MandateClaims,
    MandateLimits,
    MandateScope,
    MandateStatus,
    MandateValidity,
    MaxTxn,
    Offer,
    Period,
    PurchaseIntent,
    SpendView,
)

ISSUER = "https://api.aval.example"
MERCHANT = "vuelaya"
DEFAULT_USER = "usr_marta"
DEFAULT_AGENT = "agt_flights"


def agent_key() -> jwk.JWK:
    """A fresh Ed25519 key pair for a test agent."""
    return generate_ed25519()


def mandate(
    *,
    jti: str | None = None,
    user_id: str = DEFAULT_USER,
    agent_id: str = DEFAULT_AGENT,
    agent_jwk: dict[str, Any] | None = None,
    max_per_txn: Decimal | str = "150",
    total_budget: Decimal | str = "400",
    max_txn_count: int = 3,
    period: Period = Period.MONTH,
    categories: list[str] | None = None,
    merchants: list[str] | None = None,
    currency: str = "USD",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    payment_method_ref: str = "ynt_test_instrument",
    conditions: dict[str, Any] | None = None,
    parent_jti: str | None = None,
) -> MandateClaims:
    """The canonical VuelaYa mandate, overridable field by field."""
    now = datetime.now(UTC)
    not_before = valid_from or (now - timedelta(days=1))
    expires_at = valid_until or (now + timedelta(days=30))

    if agent_jwk is None:
        agent_jwk = public_jwk(agent_key())

    return MandateClaims(
        iss=ISSUER,
        iat=int(not_before.timestamp()),
        nbf=int(not_before.timestamp()),
        exp=int(expires_at.timestamp()),
        jti=jti or ids.new_id(ids.MANDATE),
        sub=user_id,
        agent=agent_id,
        cnf=ConfirmationKey(jwk=agent_jwk),
        payment_method_ref=payment_method_ref,
        currency=currency,
        scope=MandateScope(
            categories=categories if categories is not None else ["flights"],
            merchants=merchants if merchants is not None else [MERCHANT],
        ),
        conditions=conditions if conditions is not None
        else {"<": [{"var": "offer.price"}, 150]},
        limits=MandateLimits(
            max_per_txn=Decimal(str(max_per_txn)),
            total_budget=Decimal(str(total_budget)),
            max_txn=MaxTxn(count=max_txn_count, period=period),
        ),
        validity=MandateValidity(not_before=not_before, expires_at=expires_at),
        parent_jti=parent_jti,
    )


def offer(
    *,
    offer_id: str | None = None,
    amount: str = "130.00",
    category: str = "flights",
    merchant_id: str = MERCHANT,
    title: str = "BOG->COR morning flight",
    description: str | None = None,
) -> Offer:
    return Offer(
        offer_id=offer_id or ids.new_id(ids.OFFER),
        merchant_id=merchant_id,
        category=category,
        title=title,
        amount=amount,
        currency="USD",
        description=description,
    )


def intent(
    *,
    mandate_jti: str,
    offer_id: str | None = None,
    amount: str = "130.00",
    agent_id: str = DEFAULT_AGENT,
    merchant_id: str = MERCHANT,
    currency: str = "USD",
    ttl: int = 120,
    now: int | None = None,
    checkout_hash: str | None = None,
) -> PurchaseIntent:
    """A purchase intent. `exp - iat <= 120s` per schemas.md §2."""
    issued = now if now is not None else int(time.time())
    return PurchaseIntent(
        mandate_jti=mandate_jti,
        agent=agent_id,
        merchant_id=merchant_id,
        offer_id=offer_id or ids.new_id(ids.OFFER),
        amount=amount,
        currency=currency,
        nonce=ids.new_id("non"),
        jti=ids.new_id(ids.INTENT),
        iat=issued,
        exp=issued + ttl,
        checkout_hash=checkout_hash,
    )


def spend(
    *,
    spent_total: Decimal | str = "0",
    reserved_total: Decimal | str = "0",
    txn_count_period: int = 0,
    status: MandateStatus = MandateStatus.ACTIVE,
) -> SpendView:
    return SpendView(
        spent_total=Decimal(str(spent_total)),
        reserved_total=Decimal(str(reserved_total)),
        txn_count_period=txn_count_period,
        mandate_status=status,
    )
