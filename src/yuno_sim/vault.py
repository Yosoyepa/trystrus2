"""Instrument enrollment and the rail-side kill switch.

The shape follows the vaulting flow of ADR-007, which decision 0024 kept even
though the provider changed: the human approves once, a durable token is
issued, and every later charge quotes the token. Nobody downstream ever sees
an instrument.

Two properties are load-bearing rather than incidental:

* **A payment token does not expire.** It lives until deleted, which is
  exactly the semantics revocation needs (decision #4). A token with a TTL
  would make "revoked" and "expired" indistinguishable in the trail.
* **Deletion is idempotent.** Revocation may be retried, and a second DELETE
  that errored would make a successful revocation look failed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib import ids

from .config import settings
from .models import PaymentTokenRow, SetupTokenRow

SETUP_TOKEN_TTL_DAYS = 3


class VaultError(Exception):
    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message)


async def create_setup_token(session: AsyncSession, mandate_id: str) -> SetupTokenRow:
    """Begin enrollment. Returns the URL where the human approves."""
    setup_token_id = ids.new_id("yst")
    row = SetupTokenRow(
        setup_token_id=setup_token_id,
        mandate_id=mandate_id,
        status="pending",
        approve_url=f"{settings().approval_base_url}/{setup_token_id}",
        expires_at=datetime.now(UTC) + timedelta(days=SETUP_TOKEN_TTL_DAYS),
    )
    session.add(row)
    await session.flush()
    return row


async def approve_setup_token(session: AsyncSession, setup_token_id: str) -> None:
    """Stand-in for the human approving inside the provider's own UI.

    In the real flow this is a redirect the buyer completes at the provider —
    the "authentication moment". Here it is an endpoint, and it is the one
    place the simulation is thinner than reality: we cannot make a judge
    authenticate against a provider that does not exist.
    """
    result = await session.execute(
        update(SetupTokenRow)
        .where(SetupTokenRow.setup_token_id == setup_token_id, SetupTokenRow.status == "pending")
        .values(status="approved")
        .returning(SetupTokenRow.setup_token_id)
    )
    if result.scalar_one_or_none() is None:
        raise VaultError(f"setup token {setup_token_id} is not pending")


async def exchange(session: AsyncSession, setup_token_id: str) -> PaymentTokenRow:
    """Approved setup token -> durable payment token."""
    row = await session.get(SetupTokenRow, setup_token_id)
    if row is None:
        raise VaultError("unknown setup token")
    if row.status == "exchanged":
        raise VaultError("setup token already exchanged")
    if row.status != "approved":
        raise VaultError(
            "setup token has not been approved by the human yet — "
            "the whole point of enrollment is that a person agreed once"
        )
    if row.expires_at < datetime.now(UTC):
        raise VaultError("setup token expired")

    token = PaymentTokenRow(
        token_id=ids.new_id(ids.YUNO_TOKEN),
        setup_token_id=setup_token_id,
        mandate_id=row.mandate_id,
        status="active",
        instrument_label="VISA ****4242 (simulated)",
    )
    session.add(token)
    await session.execute(
        update(SetupTokenRow)
        .where(SetupTokenRow.setup_token_id == setup_token_id)
        .values(status="exchanged")
    )
    await session.flush()
    return token


async def get_active_token(session: AsyncSession, token_id: str) -> PaymentTokenRow | None:
    result = await session.execute(
        select(PaymentTokenRow).where(
            PaymentTokenRow.token_id == token_id, PaymentTokenRow.status == "active"
        )
    )
    return result.scalar_one_or_none()


async def delete_token(session: AsyncSession, token_id: str) -> bool:
    """The rail-side kill switch. Returns whether this call did the deleting.

    Guarded UPDATE for the same reason the mandate state machine uses one: a
    concurrent charge must not read an active token that is being deleted.
    """
    result = await session.execute(
        update(PaymentTokenRow)
        .where(PaymentTokenRow.token_id == token_id, PaymentTokenRow.status == "active")
        .values(status="deleted", deleted_at=datetime.now(UTC))
        .returning(PaymentTokenRow.token_id)
    )
    return result.scalar_one_or_none() is not None


async def token_exists(session: AsyncSession, token_id: str) -> bool:
    return await session.get(PaymentTokenRow, token_id) is not None
