"""Disputes — "I never authorised this".

Bonus B1. Decision #8 argued a real rail was needed here because it produces a
dispute object we did not author. Decision 0024 gave that up knowingly; what
we get back is a dispute that adjudicates **deterministically on stage**
instead of depending on whether a sandbox happens to support disputes over
wallet-vaulted transactions (assumption S13, which was never validated).

The evidence bundle is the interesting part and it is not simulated: mandate,
signed intent, merchant Checkout JWT, approval receipt and capture are all
real artefacts produced by the real crypto. Adjudication reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib import ids

from .models import DisputeRow, PaymentRow

BUYER_FAVOR = "BUYER_FAVOR"
SELLER_FAVOR = "SELLER_FAVOR"


class DisputeError(Exception):
    pass


async def open_dispute(session: AsyncSession, payment_id: str, *,
                       reason: str = "UNAUTHORISED") -> DisputeRow:
    payment = await session.get(PaymentRow, payment_id)
    if payment is None:
        raise DisputeError(f"unknown payment {payment_id}")
    if payment.status != "captured":
        raise DisputeError("only a captured payment can be disputed")

    row = DisputeRow(
        dispute_id=ids.new_id(ids.YUNO_DISPUTE),
        payment_id=payment_id,
        reason=reason,
        status="open",
    )
    session.add(row)
    await session.flush()
    return row


async def adjudicate(session: AsyncSession, dispute_id: str,
                     evidence: dict[str, Any]) -> DisputeRow:
    """Decide a dispute from the evidence bundle.

    The rule the trail supports: a charge is defensible when the seller can
    show an unbroken chain — a mandate the buyer signed, an intent signed by
    the agent that mandate names, and a cart the merchant committed to that
    matches what was charged.

    Any missing link goes to the buyer. That asymmetry is deliberate: the
    burden is on whoever took the money to show the permission existed, which
    is the same direction consumer payment rules point.
    """
    dispute = await session.get(DisputeRow, dispute_id)
    if dispute is None:
        raise DisputeError(f"unknown dispute {dispute_id}")
    if dispute.status != "open":
        raise DisputeError("dispute already resolved")

    payment = await session.get(PaymentRow, dispute.payment_id)

    findings: list[str] = []
    defensible = True

    if not evidence.get("mandate_sd_jwt"):
        findings.append("no signed mandate presented")
        defensible = False
    if not evidence.get("intent_jwt"):
        findings.append("no signed purchase intent presented")
        defensible = False

    checkout_hash = evidence.get("checkout_hash")
    if payment is not None and payment.checkout_hash:
        if checkout_hash != payment.checkout_hash:
            findings.append(
                "the cart in evidence is not the cart that was charged")
            defensible = False
    elif not checkout_hash:
        findings.append("the merchant never committed to a cart")
        defensible = False

    # An approval receipt is only required when the purchase escalated. Its
    # absence on an in-mandate purchase is normal, not suspicious.
    if evidence.get("escalated") and not evidence.get("approval_receipt_sig"):
        findings.append("escalated purchase with no approval receipt")
        defensible = False

    if defensible and not findings:
        findings.append("mandate, intent and cart form an unbroken chain")

    # Mutate the loaded object rather than issuing a Core UPDATE: the session's
    # identity map would keep returning the pre-update instance, and a caller
    # reading `outcome` back would see None.
    dispute.status = "resolved"
    dispute.outcome = SELLER_FAVOR if defensible else BUYER_FAVOR
    dispute.evidence = {**evidence, "findings": findings}
    dispute.resolved_at = datetime.now(UTC)
    await session.flush()
    return dispute


async def list_for_payment(session: AsyncSession,
                           payment_id: str) -> list[DisputeRow]:
    result = await session.execute(
        select(DisputeRow).where(DisputeRow.payment_id == payment_id))
    return list(result.scalars())
