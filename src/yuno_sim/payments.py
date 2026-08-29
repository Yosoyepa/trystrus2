"""Settlement — where the AP2 verification actually gates the money.

The order is the argument:

    idempotency -> token alive -> AP2 mandate verification -> settle

Idempotency comes first so a retry never re-verifies and never re-charges.
The token check comes before verification because a deleted token is cheaper
to detect and is the revocation path we most want to be fast. Verification
comes last before settling, so it is the closest thing to the money.

A refusal is written to `yuno_payments` with its reason, not just returned.
A rail that only records successes cannot answer "why was I not charged?",
which is half of what a dispute needs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib import ids
from trustlib.models import ReasonCode

from .ap2_verifier import AP2Verifier
from .models import IdempotencyRow, PaymentRow
from .vault import get_active_token, token_exists

log = logging.getLogger(__name__)


class PaymentRefused(Exception):
    """Settlement refused, with the reason the buyer and merchant will see."""

    def __init__(self, reason_code: ReasonCode, message: str,
                 *, payment_id: str | None = None) -> None:
        self.reason_code = reason_code
        self.payment_id = payment_id
        super().__init__(message)


@dataclass(frozen=True)
class Settlement:
    payment_id: str
    amount: Decimal
    currency: str
    mandate_jti: str
    captured_at: datetime
    replayed: bool = False

    def to_receipt(self, *, purchase_id: str) -> dict:
        return {
            "purchase_id": purchase_id,
            "capture_id": self.payment_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "captured_at": self.captured_at.isoformat(),
            "mandate_jti": self.mandate_jti,
            "simulated": True,
        }


async def capture(
    session: AsyncSession,
    verifier: AP2Verifier,
    *,
    token_id: str,
    amount: Decimal,
    currency: str,
    idempotency_key: str,
    intent_ref: str,
    purchase_id: str,
    mandate_sd_jwt: str | None = None,
    checkout_jwt: str | None = None,
) -> Settlement:
    # ---- 1. idempotency -------------------------------------------------
    # Checked before anything else so a retried request cannot double charge
    # even if the first attempt is still in flight (the PK does the work).
    replay = await session.get(IdempotencyRow, idempotency_key)
    if replay is not None:
        stored = replay.response
        log.info("idempotent replay of %s", idempotency_key)
        return Settlement(
            payment_id=stored["capture_id"],
            amount=Decimal(stored["amount"]),
            currency=stored["currency"],
            mandate_jti=stored["mandate_jti"],
            captured_at=datetime.fromisoformat(stored["captured_at"]),
            replayed=True,
        )

    # ---- 2. is the instrument still there? ------------------------------
    token = await get_active_token(session, token_id)
    if token is None:
        reason = (ReasonCode.RAIL_TOKEN_DELETED
                  if await token_exists(session, token_id)
                  else ReasonCode.RAIL_ERROR)
        await _record_refusal(session, token_id=token_id, amount=amount,
                              currency=currency, reason=reason,
                              intent_ref=intent_ref, mandate_jti="unknown")
        raise PaymentRefused(
            reason,
            "the payment token was deleted — this is the rail-side half of "
            "revocation" if reason is ReasonCode.RAIL_TOKEN_DELETED
            else "unknown payment token",
        )

    # ---- 3. the AP2 Payment Mandate -------------------------------------
    verdict = await verifier.verify(mandate_sd_jwt=mandate_sd_jwt,
                                    checkout_jwt=checkout_jwt,
                                    amount=amount, currency=currency)
    if not verdict.ok:
        await _record_refusal(session, token_id=token_id, amount=amount,
                              currency=currency, reason=verdict.reason_code,
                              intent_ref=intent_ref,
                              mandate_jti=verdict.mandate_jti or "unknown",
                              checkout_hash=verdict.checkout_hash)
        raise PaymentRefused(verdict.reason_code, verdict.detail or "refused")

    # ---- 4. settle -------------------------------------------------------
    payment_id = ids.new_id(ids.YUNO_PAYMENT)
    captured_at = datetime.now(UTC)
    session.add(PaymentRow(
        payment_id=payment_id,
        token_id=token_id,
        mandate_jti=verdict.mandate_jti,
        amount=amount,
        currency=currency,
        status="captured",
        checkout_hash=verdict.checkout_hash,
        intent_ref=intent_ref,
    ))

    settlement = Settlement(payment_id=payment_id, amount=amount,
                            currency=currency, mandate_jti=verdict.mandate_jti,
                            captured_at=captured_at)

    session.add(IdempotencyRow(
        idempotency_key=idempotency_key,
        payment_id=payment_id,
        response=settlement.to_receipt(purchase_id=purchase_id),
    ))

    try:
        await session.flush()
    except IntegrityError:
        # Two requests with the same key raced past the read in step 1. The
        # primary key settles it: roll back and return the winner's result.
        await session.rollback()
        winner = await session.get(IdempotencyRow, idempotency_key)
        if winner is None:
            raise
        stored = winner.response
        return Settlement(
            payment_id=stored["capture_id"], amount=Decimal(stored["amount"]),
            currency=stored["currency"], mandate_jti=stored["mandate_jti"],
            captured_at=datetime.fromisoformat(stored["captured_at"]),
            replayed=True,
        )

    return settlement


async def _record_refusal(session: AsyncSession, *, token_id: str,
                          amount: Decimal, currency: str,
                          reason: ReasonCode | None, intent_ref: str,
                          mandate_jti: str,
                          checkout_hash: str | None = None) -> None:
    """Write the refusal down. "Why was I not charged?" is a real question."""
    session.add(PaymentRow(
        payment_id=ids.new_id(ids.YUNO_PAYMENT),
        token_id=token_id,
        mandate_jti=mandate_jti,
        amount=amount,
        currency=currency,
        status="refused",
        reason_code=reason.value if reason else None,
        checkout_hash=checkout_hash,
        intent_ref=intent_ref,
    ))
    await session.flush()
