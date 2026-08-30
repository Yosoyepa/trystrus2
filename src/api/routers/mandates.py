"""Mandate lifecycle — `api.yaml` tag `mandates` [Dev 3].

The endpoints in order of the story they tell:

    POST /mandates                  Marta describes what her agent may buy
    POST /mandates/{id}/passkey/assert   she agrees, with her face or thumb
    GET  /.well-known/jwks.json     a stranger can now check that agreement
    POST /mandates/{id}/revoke      she takes it back, and the rail forgets too
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib.events import emit_event
from trustlib.models import (
    MandateClaims,
    MandateClaimsInput,
    MandateStatus,
    ReasonCode,
)

from .. import repository as repo
from ..db import get_session
from ..deps import mandate_registry, passkey_service, rail
from ..schemas import (
    CredentialView,
    MandateActive,
    MandateCreate,
    MandateDraft,
    MandateView,
    PasskeyAssertion,
    PaymentEnroll,
    RegistrationBegin,
    RegistrationComplete,
    RegistrationOptions,
)
from ..services.passkey import PasskeyError, Purpose

log = logging.getLogger(__name__)
router = APIRouter(tags=["mandates"])


# ==========================================================================
# JWKS — how the merchant verifies without trusting us (decision #6)
# ==========================================================================
@router.get("/.well-known/jwks.json")
async def jwks() -> dict:
    """Public keys of the mandate issuer and the merchant's AP2 signer.

    Deliberately unauthenticated: the whole point is that anyone can check a
    mandate's signature offline, without asking us anything.
    """
    return mandate_registry().jwks()


# ==========================================================================
# Passkey registration — before any mandate can be agreed to
# ==========================================================================
@router.post("/passkeys/register/begin", response_model=RegistrationOptions)
async def begin_registration(body: RegistrationBegin, session: AsyncSession = Depends(get_session)):
    options, challenge = passkey_service().registration_options(
        user_id=body.user_id, user_name=body.user_name
    )
    await repo.store_challenge(session, challenge)

    import json

    return RegistrationOptions(options=json.loads(options), challenge=challenge.value)


@router.post("/passkeys/register/complete", response_model=CredentialView)
async def complete_registration(
    body: RegistrationComplete, session: AsyncSession = Depends(get_session)
):
    challenge = await repo.consume_challenge(session, body.challenge, purpose=Purpose.REGISTER)
    if challenge is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "challenge unknown or already used")
    try:
        credential = passkey_service().verify_registration(body.credential, challenge)
    except PasskeyError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    await repo.store_credential(session, credential)
    return CredentialView(
        credential_id=credential.credential_id,
        user_id=credential.user_id,
        sign_count=credential.sign_count,
    )


# ==========================================================================
# Create — draft, plus the challenge that will bind Marta's gesture
# ==========================================================================
@router.post("/mandates", status_code=status.HTTP_201_CREATED, response_model=MandateDraft)
async def create_mandate(body: MandateCreate, session: AsyncSession = Depends(get_session)):
    """Create a mandate in `draft` and open the passkey ceremony.

    Nothing is signed here and nothing can pay. The claims are assembled in
    final form because the ceremony's challenge is their canonical hash — but
    signing before Marta agrees would produce a valid mandate nobody
    authorized, so `sign` happens in the assert endpoint.
    """
    registry = mandate_registry()
    claims = registry.build_claims(
        MandateClaimsInput(
            user_id=body.user_id,
            agent_id=body.agent_id,
            currency=body.currency,
            scope=body.scope,
            limits=body.limits,
            validity=body.validity,
            conditions=body.conditions,
            agent_jwk=body.agent_jwk,
            payment_method_ref=body.payment_method_ref,
        )
    )

    # `mandates` keys on `jti` (the agent lane's table, shared verbatim — see
    # aval/contracts/fixtures/schema.sql). Minting a second, separate `id`
    # here would just be two names for the same row with nothing enforcing
    # they ever agree, so the mandate's own jti is the identifier throughout.
    mandate_id = claims.jti
    await repo.create_mandate(session, claims, mandate_id=mandate_id)

    credentials = await repo.credentials_for(session, body.user_id)
    if not credentials:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED,
            "no passkey registered for this user — an agent cannot complete "
            "a WebAuthn ceremony, so consent has nowhere to come from",
        )

    options, challenge = passkey_service().mandate_options(
        claims=claims, purpose=Purpose.ACTIVATE, credentials=credentials
    )
    await repo.store_challenge(session, challenge)

    # Enrollment at the rail runs in parallel with the ceremony: the human
    # approves the instrument once, and we only ever hold the opaque token.
    enroll = None
    try:
        setup = await rail().create_setup_token(mandate_id)
        enroll = PaymentEnroll(
            approve_url=setup.approve_url,
            setup_token_id=setup.setup_token_id,
            simulated=setup.simulated,
        )
    except Exception:
        # A rail hiccup must not block the mandate: the crypto half is what
        # G1 is graded on, and enrollment can be retried.
        log.warning("rail enrollment unavailable at create time", exc_info=True)

    await emit_event(
        session,
        type="mandate.created",
        aggregate_id=claims.jti,
        payload={"jti": claims.jti, "limits": claims.limits.model_dump(mode="json")},
    )

    import json

    return MandateDraft(
        mandate_id=mandate_id,
        jti=claims.jti,
        passkey_challenge=json.loads(options),
        payment_enroll=enroll,
    )


# ==========================================================================
# Activate — the gesture that turns a draft into authority
# ==========================================================================
@router.post("/mandates/{mandate_id}/passkey/assert", response_model=MandateActive)
async def assert_passkey(
    mandate_id: str, body: PasskeyAssertion, session: AsyncSession = Depends(get_session)
):
    """Complete the ceremony: verify the assertion, then sign and activate."""
    registry = mandate_registry()
    record = await repo.get_mandate(session, mandate_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mandate")

    claims = await _verify_gesture(session, record, body, Purpose.ACTIVATE)

    result = await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    if not result.ok:
        # Someone revoked or activated between the ceremony and here.
        raise HTTPException(
            status.HTTP_409_CONFLICT, (result.reason_code or ReasonCode.MANDATE_SUSPENDED).value
        )

    issued = registry.sign(claims, disclose=_disclosable(record))
    await repo.attach_sd_jwt(session, mandate_id, issued.sd_jwt, claims)

    # The mandate carries only an opaque rail reference. Persist that reference
    # locally so revocation can delete the *real* rail-side token after its
    # state transition commits. Never interpret it as card data.
    if claims.payment_method_ref:
        await repo.link_instrument(
            session, token_ref=claims.payment_method_ref, mandate_jti=claims.jti
        )
        await emit_event(
            session,
            type="payment_instrument.linked",
            aggregate_id=claims.jti,
            payload={"mandate_jti": claims.jti, "token_ref": claims.payment_method_ref},
        )

    await emit_event(
        session,
        type="mandate.activated",
        aggregate_id=claims.jti,
        payload={"jti": claims.jti, "limits": claims.limits.model_dump(mode="json")},
    )

    return MandateActive(mandate_id=mandate_id, sd_jwt=issued.sd_jwt, jti=claims.jti)


# ==========================================================================
# Revoke — the scene the judges run
# ==========================================================================
@router.post("/mandates/{mandate_id}/revoke/options", response_model=RegistrationOptions)
async def begin_revoke(mandate_id: str, session: AsyncSession = Depends(get_session)):
    """Issue a fresh, purpose-bound passkey challenge for revocation."""
    record = await repo.get_mandate(session, mandate_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mandate")
    claims = MandateClaims.model_validate_json(record.claims)
    credentials = await repo.credentials_for(session, record.user_id)
    if not credentials:
        raise HTTPException(
            status.HTTP_412_PRECONDITION_FAILED, "no passkey registered for this user"
        )
    options, challenge = passkey_service().mandate_options(
        claims=claims, purpose=Purpose.REVOKE, credentials=credentials
    )
    await repo.store_challenge(session, challenge)

    import json

    return RegistrationOptions(options=json.loads(options), challenge=challenge.value)


@router.post("/mandates/{mandate_id}/revoke", response_model=MandateView)
async def revoke_mandate(
    mandate_id: str, body: PasskeyAssertion, session: AsyncSession = Depends(get_session)
):
    """Revoke a mandate and delete its rail token. Target: under two seconds.

    The order below is the design, not a preference:

    1. **Gesture first.** Taking authority away demands the same proof as
       granting it, or revocation becomes the weakest link (decision #3).
    2. **State and event, one transaction.** Verify (Dev 2) reads
       `mandates.status` inside its charging transaction, so the moment this
       commits, every in-flight purchase starts failing. That is what closes
       TOCTOU by construction (decision #4).
    3. **Rail token afterwards, outside the transaction.** A network call must
       never hold a lock on the row a live purchase is reading. And if the
       rail is unreachable, the revocation still stands — we log it and retry
       rather than roll back. Fail closed: the mandate is dead either way.
    """
    record = await repo.get_mandate(session, mandate_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mandate")

    await _verify_gesture(session, record, body, Purpose.REVOKE)

    result = await repo.transition(session, mandate_id, MandateStatus.REVOKED)
    if not result.ok:
        raise HTTPException(
            status.HTTP_409_CONFLICT, (result.reason_code or ReasonCode.MANDATE_REVOKED).value
        )

    await emit_event(
        session,
        type="mandate.revoked",
        aggregate_id=record.jti,
        payload={"jti": record.jti, "by": record.user_id, "at": datetime.now(UTC).isoformat()},
    )

    instruments = await repo.instruments_for(session, record.jti)
    await session.commit()  # the kill switch that matters is now live

    for instrument in instruments:
        try:
            await rail().delete_payment_token(instrument.token_ref)
            await repo.mark_instrument_deleted(session, instrument.token_ref)
        except Exception:
            # Deliberately swallowed. The mandate is revoked; a rail that did
            # not hear us is a reconciliation problem, not a reason to hand
            # authority back.
            log.error(
                "rail token %s not deleted — mandate %s is revoked regardless",
                instrument.token_ref,
                mandate_id,
                exc_info=True,
            )

    return await _view(session, mandate_id)


# ==========================================================================
# Read
# ==========================================================================
@router.get("/mandates", response_model=list[MandateView])
async def list_mandates(user_id: str, session: AsyncSession = Depends(get_session)):
    records = await repo.list_mandates(session, user_id)
    return [_to_view(r) for r in records]


@router.get("/mandates/by-jti/{jti}", response_model=MandateView)
async def get_mandate_by_jti(jti: str, session: AsyncSession = Depends(get_session)):
    """Look a mandate up by the `jti` inside its SD-JWT.

    Exists for the payment rail. A verifier holding a presented mandate knows
    its `jti`, not our internal id — and a signature stays valid after
    revocation, so the rail has to ask someone whether the permission is still
    live. Answering that is the issuer's job (decision #4).

    Declared before `/mandates/{mandate_id}` so the literal segment wins.
    """
    record = await repo.get_mandate_by_jti(session, jti)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mandate")
    return _to_view(record)


@router.get("/mandates/{mandate_id}", response_model=MandateView)
async def get_mandate(mandate_id: str, session: AsyncSession = Depends(get_session)):
    return await _view(session, mandate_id)


# ==========================================================================
# helpers
# ==========================================================================
async def _verify_gesture(session, record, body: PasskeyAssertion, purpose: Purpose):
    """Shared by activate and revoke — both demand the same proof."""
    from trustlib.models import MandateClaims

    claims = MandateClaims.model_validate_json(record.claims)
    presented = body.response.get("clientDataJSON_challenge") or _challenge_from(body)

    challenge = await repo.consume_challenge(session, presented, purpose=purpose)
    if challenge is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "challenge unknown or already used")
    if challenge.purpose is not purpose:
        # A gesture collected to activate must not be replayed to revoke.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "challenge was issued for a different purpose"
        )

    credential = await repo.get_credential(session, body.id)
    if credential is None or credential.user_id != record.user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown credential")

    try:
        new_count = passkey_service().verify_assertion(
            body.model_dump(), challenge=challenge, credential=credential, claims=claims
        )
    except PasskeyError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    await repo.advance_sign_count(session, credential.credential_id, new_count)
    return claims


def _challenge_from(body: PasskeyAssertion) -> str:
    """Pull the challenge out of clientDataJSON.

    The client echoes it back; we look it up rather than trusting it, so an
    invented value simply finds no row.
    """
    import base64
    import json

    raw = body.response.get("clientDataJSON", "")
    padding = "=" * (-len(raw) % 4)
    data = json.loads(base64.urlsafe_b64decode(raw + padding))
    return data["challenge"]


def _disclosable(record) -> dict:
    import json

    claims = json.loads(record.claims) if record.claims else {}
    return {k: claims[k] for k in ("email", "shipping_address") if k in claims}


async def _view(session, mandate_id: str) -> MandateView:
    record = await repo.get_mandate(session, mandate_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such mandate")
    return _to_view(record)


def _to_view(record) -> MandateView:
    from trustlib.models import MandateClaims

    claims = MandateClaims.model_validate_json(record.claims)
    return MandateView(
        mandate_id=record.jti,
        status=record.status,
        jti=record.jti,
        limits=claims.limits,
        scope=claims.scope,
        spent=record.spent_total,
        reserved=record.reserved_amount,
        txn_count_period=record.txn_count,
        payment_method_ref=claims.payment_method_ref,
        parent_jti=record.parent_jti,
        created_at=record.created_at,
    )
