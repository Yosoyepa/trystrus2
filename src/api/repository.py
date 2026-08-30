"""Data access for the kernel's identity tables.

The important function in this file is `transition`. Everything else is
ordinary CRUD; that one is the enforcement point for decisions #4 and #12, and
it is written as a **guarded UPDATE** rather than read-then-write on purpose:

    UPDATE mandates SET status = :to
     WHERE id = :id AND status = ANY(:allowed_sources)

A read-then-write would open a window between deciding and acting, and that
window is exactly what a judge exercises when they revoke mid-purchase. With
the guard in the statement, a concurrent revocation makes this UPDATE match
zero rows, and zero rows is a refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib.models import MandateClaims, MandateStatus

from .models import (
    Mandate,
    PaymentInstrument,
    WebAuthnChallenge,
    WebAuthnCredential,
)
from .services import state_machine as sm
from .services.passkey import Challenge, Purpose, StoredCredential


# ==========================================================================
# Mandates
# ==========================================================================
async def create_mandate(session: AsyncSession, claims: MandateClaims, *,
                         mandate_id: str) -> Mandate:
    """Persist a mandate in `draft`. It is not signed yet and cannot pay."""
    mandate = Mandate(
        id=mandate_id,
        jti=claims.jti,
        user_id=claims.sub,
        agent_id=claims.agent,
        status=MandateStatus.DRAFT.value,
        claims=claims.model_dump(mode="json", exclude_none=True),
        parent_jti=claims.parent_jti,
    )
    session.add(mandate)
    await session.flush()
    return mandate


async def get_mandate(session: AsyncSession, mandate_id: str) -> Mandate | None:
    return await session.get(Mandate, mandate_id)


async def get_mandate_by_jti(session: AsyncSession, jti: str) -> Mandate | None:
    result = await session.execute(select(Mandate).where(Mandate.jti == jti))
    return result.scalar_one_or_none()


async def list_mandates(session: AsyncSession, user_id: str) -> list[Mandate]:
    result = await session.execute(
        select(Mandate).where(Mandate.user_id == user_id)
        .order_by(Mandate.created_at.desc())
    )
    return list(result.scalars())


async def attach_sd_jwt(session: AsyncSession, mandate_id: str,
                        sd_jwt: str, claims: MandateClaims) -> None:
    """Store the signed mandate after the ceremony succeeded."""
    await session.execute(
        update(Mandate)
        .where(Mandate.id == mandate_id)
        .values(sd_jwt=sd_jwt,
                claims=claims.model_dump(mode="json", exclude_none=True),
                updated_at=datetime.now(UTC))
    )


async def transition(session: AsyncSession, mandate_id: str,
                     to: MandateStatus) -> sm.TransitionResult:
    """Move a mandate to `to`, or refuse. The whole state machine, in one SQL.

    Returns a result rather than raising: a refused transition is a normal
    outcome that the caller records in the trail, not an exceptional one.
    """
    allowed = sm.sources_for(to)
    if not allowed:
        # No legal source — the guard would match nothing anyway. Say so.
        current = await _current_status(session, mandate_id)
        return sm.TransitionResult(
            ok=False, frm=current, to=to,
            reason_code=sm.refusal_reason(current, to) if current else None)

    result = await session.execute(
        update(Mandate)
        .where(Mandate.id == mandate_id, Mandate.status.in_(allowed))
        .values(status=to.value, updated_at=datetime.now(UTC),
                version=Mandate.version + 1)
        .returning(Mandate.jti)
    )

    if result.scalar_one_or_none() is None:
        # Zero rows: either the mandate is gone, or it moved under us. Read
        # the current state only to explain the refusal — never to retry.
        current = await _current_status(session, mandate_id)
        return sm.TransitionResult(
            ok=False, frm=current, to=to,
            reason_code=sm.refusal_reason(current, to) if current else None)

    return sm.TransitionResult(ok=True, frm=None, to=to,
                               event=sm.TRANSITION_EVENTS[to])


async def _current_status(session: AsyncSession,
                          mandate_id: str) -> MandateStatus | None:
    result = await session.execute(
        select(Mandate.status).where(Mandate.id == mandate_id))
    raw = result.scalar_one_or_none()
    return MandateStatus(raw) if raw else None


# ==========================================================================
# Payment instruments
# ==========================================================================
async def link_instrument(session: AsyncSession, *, token_ref: str,
                          mandate_jti: str, rail: str = "yuno_sim") -> None:
    """Persist the opaque rail token once; activation retries stay idempotent."""
    await session.execute(
        insert(PaymentInstrument)
        .values(token_ref=token_ref, mandate_jti=mandate_jti, rail=rail,
                status="active")
        .on_conflict_do_nothing(index_elements=[PaymentInstrument.token_ref])
    )
    await session.flush()


async def instruments_for(session: AsyncSession,
                          mandate_jti: str) -> list[PaymentInstrument]:
    result = await session.execute(
        select(PaymentInstrument)
        .where(PaymentInstrument.mandate_jti == mandate_jti,
               PaymentInstrument.status == "active")
    )
    return list(result.scalars())


async def mark_instrument_deleted(session: AsyncSession, token_ref: str) -> None:
    """Record that the rail-side token is gone (decision #4's second kill switch)."""
    await session.execute(
        update(PaymentInstrument)
        .where(PaymentInstrument.token_ref == token_ref)
        .values(status="deleted", deleted_at=datetime.now(UTC))
    )


# ==========================================================================
# Passkeys (decision 0021)
# ==========================================================================
async def store_challenge(session: AsyncSession, challenge: Challenge) -> None:
    session.add(WebAuthnChallenge(
        challenge=challenge.value,
        user_id=challenge.user_id,
        mandate_id=challenge.mandate_id,
        purpose=challenge.purpose.value,
        expires_at=challenge.expires_at,
    ))
    await session.flush()


async def consume_challenge(session: AsyncSession, value: str,
                            purpose: Purpose | None = None) -> Challenge | None:
    """Claim a challenge exactly once.

    The `consumed_at IS NULL` guard is the replay defence, and it is in the
    UPDATE for the same reason the state machine's guard is: two requests
    presenting the same assertion must not both win.
    """
    result = await session.execute(
        update(WebAuthnChallenge)
        .where(WebAuthnChallenge.challenge == value,
               WebAuthnChallenge.consumed_at.is_(None),
               *([WebAuthnChallenge.purpose == purpose.value] if purpose else []))
        .values(consumed_at=datetime.now(UTC))
        .returning(WebAuthnChallenge.user_id, WebAuthnChallenge.purpose,
                   WebAuthnChallenge.mandate_id, WebAuthnChallenge.expires_at)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return Challenge(value=value, user_id=row.user_id,
                     purpose=Purpose(row.purpose), mandate_id=row.mandate_id,
                     expires_at=row.expires_at)


async def store_credential(session: AsyncSession,
                           credential: StoredCredential) -> None:
    session.add(WebAuthnCredential(
        credential_id=credential.credential_id,
        user_id=credential.user_id,
        public_key=credential.public_key,
        sign_count=credential.sign_count,
    ))
    await session.flush()


async def credentials_for(session: AsyncSession,
                          user_id: str) -> list[StoredCredential]:
    result = await session.execute(
        select(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id))
    return [
        StoredCredential(credential_id=row.credential_id, user_id=row.user_id,
                         public_key=row.public_key, sign_count=row.sign_count)
        for row in result.scalars()
    ]


async def get_credential(session: AsyncSession,
                         credential_id: str) -> StoredCredential | None:
    row = await session.get(WebAuthnCredential, credential_id)
    if row is None:
        return None
    return StoredCredential(credential_id=row.credential_id, user_id=row.user_id,
                            public_key=row.public_key, sign_count=row.sign_count)


async def advance_sign_count(session: AsyncSession, credential_id: str,
                             new_count: int) -> None:
    await session.execute(
        update(WebAuthnCredential)
        .where(WebAuthnCredential.credential_id == credential_id)
        .values(sign_count=new_count, last_used_at=datetime.now(UTC))
    )


# ==========================================================================
# Spend view — read-only here. Dev 2's verify owns these columns.
# ==========================================================================
async def spend_view(session: AsyncSession, mandate_id: str) -> dict | None:
    result = await session.execute(
        text("""SELECT status, spent_total, reserved_amount, txn_count_period
                  FROM mandates WHERE id = :id"""),
        {"id": mandate_id},
    )
    row = result.one_or_none()
    if row is None:
        return None
    return {
        "mandate_status": MandateStatus(row.status),
        "spent_total": Decimal(row.spent_total),
        "reserved_total": Decimal(row.reserved_amount),
        "txn_count_period": row.txn_count_period,
    }
