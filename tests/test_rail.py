"""T17 — the payment rail (PLAN.md §10).

"Enrollment produces a payment token; capture is idempotent (the same key
returns the same result); DELETE the token and a later charge fails."

Plus the check decision 0024 exists for, which no real rail can do today:
**a revoked mandate is refused even when its token is still alive.** That is
the second, independent kill switch — the first being the kernel's verify.

These run with no network and no credentials, which is why they can run in CI
at all. That is the compensation for giving up a real sandbox.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from trustlib import ap2, fake, ids, sdjwt
from trustlib.jose import generate_ed25519, generate_p256, public_jwk, sign_compact
from trustlib.models import MandateStatus, ReasonCode
from yuno_sim import disputes, vault
from yuno_sim.ap2_verifier import AP2Verifier
from yuno_sim.config import Settings
from yuno_sim.payments import PaymentRefused, capture

pytestmark = pytest.mark.asyncio

ISSUER_KID = "v1"
MERCHANT_KID = "m1"


# ==========================================================================
# A stand-in issuer: publishes a JWKS and answers status questions.
# ==========================================================================
class StubIssuer:
    """Plays the kernel. Lets a test revoke a mandate mid-flow."""

    def __init__(self):
        self.issuer_key = generate_ed25519()
        self.merchant_key = generate_p256()
        self.statuses: dict[str, MandateStatus] = {}
        self.reachable = True

    async def keys(self) -> dict:
        if not self.reachable:
            raise RuntimeError("issuer unreachable")
        return {ISSUER_KID: self.issuer_key, MERCHANT_KID: self.merchant_key}

    async def mandate_status(self, jti: str):
        if not self.reachable:
            return None
        return self.statuses.get(jti)

    def invalidate(self):
        pass

    # -- helpers for building the artefacts a rail would receive -----------
    def issue(self, claims=None, **kwargs) -> tuple[str, str]:
        claims = claims or fake.mandate(**kwargs)
        claims = ap2.apply_ap2_projection(claims)
        sd_jwt = sdjwt.issue(claims.model_dump(mode="json", exclude_none=True),
                             self.issuer_key, kid=ISSUER_KID)
        self.statuses[claims.jti] = MandateStatus.ACTIVE
        return sd_jwt, claims.jti

    def checkout(self, total: str = "130.00", currency: str = "USD") -> str:
        payload = ap2.build_checkout_payload(
            order_id=ids.new_id(ids.ORDER), merchant_id="vuelaya",
            merchant_name="VuelaYa",
            merchant_website="https://merchant.aval.example",
            line_items=[{"id": "ofr_COR_130", "label": "BOG->COR",
                         "amount": ap2.to_minor_units(total)}],
            total_price=total, currency=currency)
        return sign_compact(payload, self.merchant_key, kid=MERCHANT_KID,
                            typ="JWT")


@pytest.fixture
def issuer() -> StubIssuer:
    return StubIssuer()


@pytest.fixture
def verifier(issuer) -> AP2Verifier:
    return AP2Verifier(issuer=issuer, config=Settings())


@pytest_asyncio.fixture
async def token(session):
    """An enrolled, approved, active payment token."""
    setup = await vault.create_setup_token(session, "mdt_test")
    await vault.approve_setup_token(session, setup.setup_token_id)
    row = await vault.exchange(session, setup.setup_token_id)
    await session.commit()
    return row


async def charge(session, verifier, token, issuer, *, amount="130.00",
                 key=None, sd_jwt=None, checkout=None, currency="USD"):
    if sd_jwt is None:
        sd_jwt, _ = issuer.issue()
    return await capture(
        session, verifier,
        token_id=token.token_id, amount=Decimal(amount), currency=currency,
        idempotency_key=key or ids.new_id("idem"),
        intent_ref=ids.new_id(ids.INTENT), purchase_id="pur_1",
        mandate_sd_jwt=sd_jwt,
        checkout_jwt=checkout if checkout is not None else issuer.checkout(amount),
    )


# ==========================================================================
# Enrollment
# ==========================================================================
async def test_enrollment_produces_a_payment_token(session):
    setup = await vault.create_setup_token(session, "mdt_1")

    assert setup.approve_url.endswith(setup.setup_token_id)
    assert setup.status == "pending"

    await vault.approve_setup_token(session, setup.setup_token_id)
    token = await vault.exchange(session, setup.setup_token_id)

    assert token.token_id.startswith("ynt_")
    assert token.status == "active"


async def test_an_unapproved_setup_token_cannot_be_exchanged(session):
    """The human's one-time approval is the whole point of enrollment."""
    setup = await vault.create_setup_token(session, "mdt_1")

    with pytest.raises(vault.VaultError, match="not been approved"):
        await vault.exchange(session, setup.setup_token_id)


async def test_a_setup_token_cannot_be_exchanged_twice(session):
    setup = await vault.create_setup_token(session, "mdt_1")
    await vault.approve_setup_token(session, setup.setup_token_id)
    await vault.exchange(session, setup.setup_token_id)

    with pytest.raises(vault.VaultError, match="already exchanged"):
        await vault.exchange(session, setup.setup_token_id)


# ==========================================================================
# Capture
# ==========================================================================
async def test_an_in_mandate_charge_settles(session, verifier, token, issuer):
    settlement = await charge(session, verifier, token, issuer)

    assert settlement.payment_id.startswith("ynp_")
    assert settlement.amount == Decimal("130.00")
    assert not settlement.replayed


async def test_capture_is_idempotent(session, verifier, token, issuer):
    """The same key returns the same result — no second charge."""
    key = "idem-fixed"
    sd_jwt, _ = issuer.issue()
    checkout = issuer.checkout()

    first = await charge(session, verifier, token, issuer, key=key,
                         sd_jwt=sd_jwt, checkout=checkout)
    await session.commit()
    second = await charge(session, verifier, token, issuer, key=key,
                          sd_jwt=sd_jwt, checkout=checkout)

    assert second.payment_id == first.payment_id
    assert second.replayed
    assert not first.replayed


async def test_concurrent_retries_charge_once(session, verifier, token, issuer,
                                              engine):
    """Two in-flight requests with one key: the primary key settles it."""
    sd_jwt, _ = issuer.issue()
    checkout = issuer.checkout()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt():
        async with factory() as s:
            settlement = await capture(
                s, verifier, token_id=token.token_id, amount=Decimal("130.00"),
                currency="USD", idempotency_key="idem-race",
                intent_ref="int_1", purchase_id="pur_1",
                mandate_sd_jwt=sd_jwt, checkout_jwt=checkout)
            await s.commit()
            return settlement.payment_id

    first, second = await asyncio.gather(attempt(), attempt())
    assert first == second


# ==========================================================================
# The two kill switches
# ==========================================================================
async def test_a_deleted_token_stops_charging(session, verifier, token, issuer):
    """Kill switch one: the rail forgets the instrument."""
    assert await vault.delete_token(session, token.token_id)
    await session.commit()

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer)

    assert refused.value.reason_code is ReasonCode.RAIL_TOKEN_DELETED


async def test_deleting_a_token_twice_is_not_an_error(session, token):
    """Revocation retries; the second DELETE must not look like a failure."""
    assert await vault.delete_token(session, token.token_id) is True
    assert await vault.delete_token(session, token.token_id) is False
    assert await vault.token_exists(session, token.token_id)


async def test_a_revoked_mandate_is_refused_even_with_a_live_token(
        session, verifier, token, issuer):
    """Kill switch two — the one no real rail has today.

    The token is untouched and would happily charge. The mandate behind it was
    revoked, so this rail refuses anyway. That is decision 0024's whole
    argument, and it is why revocation survives a merchant that skipped the
    kernel's verify.
    """
    sd_jwt, jti = issuer.issue()
    issuer.statuses[jti] = MandateStatus.REVOKED

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, sd_jwt=sd_jwt)

    assert refused.value.reason_code is ReasonCode.MANDATE_REVOKED
    assert await vault.get_active_token(session, token.token_id) is not None


@pytest.mark.parametrize(("status", "expected"), [
    (MandateStatus.SUSPENDED, ReasonCode.MANDATE_SUSPENDED),
    (MandateStatus.EXHAUSTED, ReasonCode.MANDATE_EXHAUSTED),
    (MandateStatus.EXPIRED, ReasonCode.MANDATE_EXPIRED),
    (MandateStatus.DRAFT, ReasonCode.MANDATE_SUSPENDED),
])
async def test_only_an_active_mandate_settles(session, verifier, token, issuer,
                                              status, expected):
    sd_jwt, jti = issuer.issue()
    issuer.statuses[jti] = status

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, sd_jwt=sd_jwt)

    assert refused.value.reason_code is expected


async def test_an_unknown_mandate_is_refused(session, verifier, token, issuer):
    """The issuer has never heard of it — that settles nothing."""
    sd_jwt, jti = issuer.issue()
    del issuer.statuses[jti]

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, sd_jwt=sd_jwt)

    assert refused.value.reason_code is ReasonCode.RAIL_ERROR


async def test_an_unreachable_issuer_refuses_rather_than_assumes(
        session, verifier, token, issuer):
    """Fail closed (decision #13): "cannot know" settles nothing."""
    sd_jwt, _ = issuer.issue()
    checkout = issuer.checkout()
    issuer.reachable = False

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, sd_jwt=sd_jwt,
                     checkout=checkout)

    assert refused.value.reason_code is ReasonCode.RAIL_ERROR


# ==========================================================================
# AP2 verification
# ==========================================================================
async def test_charging_without_a_mandate_is_refused(session, verifier, token):
    """This rail does not charge on a merchant's word alone."""
    with pytest.raises(PaymentRefused) as refused:
        await capture(session, verifier, token_id=token.token_id,
                      amount=Decimal("130.00"), currency="USD",
                      idempotency_key="k1", intent_ref="int_1",
                      purchase_id="pur_1", mandate_sd_jwt=None)

    assert refused.value.reason_code is ReasonCode.INVALID_SIGNATURE


async def test_a_mandate_from_another_issuer_is_refused(session, verifier,
                                                        token, issuer):
    impostor = generate_ed25519()
    claims = ap2.apply_ap2_projection(fake.mandate())
    forged = sdjwt.issue(claims.model_dump(mode="json", exclude_none=True),
                         impostor, kid=ISSUER_KID)
    issuer.statuses[claims.jti] = MandateStatus.ACTIVE

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, sd_jwt=forged)

    assert refused.value.reason_code is ReasonCode.INVALID_SIGNATURE


async def test_an_expired_mandate_is_refused(session, verifier, token, issuer):
    import time

    claims = fake.mandate()
    past = int(time.time()) - 3600
    expired = claims.model_copy(update={"nbf": past - 60, "exp": past})
    sd_jwt, _ = issuer.issue(claims=expired)

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, sd_jwt=sd_jwt)

    assert refused.value.reason_code is ReasonCode.MANDATE_EXPIRED


async def test_a_restated_cart_is_refused(session, verifier, token, issuer):
    """The merchant signs $130, then tries to charge $300.

    Field-by-field comparison can be fooled by whatever it forgot to check;
    a signed total cannot be quietly restated.
    """
    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, amount="300.00",
                     checkout=issuer.checkout("130.00"))

    assert refused.value.reason_code is ReasonCode.CONDITION_FAILED


async def test_a_checkout_signed_by_an_unknown_key_is_refused(
        session, verifier, token, issuer):
    stranger = generate_p256()
    payload = ap2.build_checkout_payload(
        order_id="ord_x", merchant_id="vuelaya", merchant_name="VuelaYa",
        merchant_website="https://x", line_items=[], total_price="130.00",
        currency="USD")
    forged = sign_compact(payload, stranger, kid="unknown-kid", typ="JWT")

    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, checkout=forged)

    assert refused.value.reason_code is ReasonCode.INVALID_SIGNATURE


async def test_a_currency_mismatch_is_refused(session, verifier, token, issuer):
    with pytest.raises(PaymentRefused) as refused:
        await charge(session, verifier, token, issuer, currency="EUR",
                     checkout=issuer.checkout("130.00", currency="USD"))

    assert refused.value.reason_code is ReasonCode.CONDITION_FAILED


# ==========================================================================
# Refusals are written down
# ==========================================================================
async def test_a_refusal_is_recorded_with_its_reason(session, verifier, token,
                                                     issuer):
    """"Why was I not charged?" is half of what a dispute needs."""
    from sqlalchemy import select

    from yuno_sim.models import PaymentRow

    sd_jwt, jti = issuer.issue()
    issuer.statuses[jti] = MandateStatus.REVOKED
    with pytest.raises(PaymentRefused):
        await charge(session, verifier, token, issuer, sd_jwt=sd_jwt)
    await session.commit()

    rows = (await session.execute(
        select(PaymentRow).where(PaymentRow.status == "refused"))).scalars().all()

    assert len(rows) == 1
    assert rows[0].reason_code == ReasonCode.MANDATE_REVOKED.value


# ==========================================================================
# T18 — disputes (with Dev 2)
# ==========================================================================
async def test_a_complete_evidence_chain_favours_the_seller(session, verifier,
                                                            token, issuer):
    sd_jwt, jti = issuer.issue()
    checkout = issuer.checkout()
    settlement = await charge(session, verifier, token, issuer, sd_jwt=sd_jwt,
                              checkout=checkout)
    await session.commit()

    dispute = await disputes.open_dispute(session, settlement.payment_id)
    resolved = await disputes.adjudicate(session, dispute.dispute_id, {
        "mandate_sd_jwt": sd_jwt,
        "intent_jwt": "signed-intent",
        "checkout_hash": ap2.checkout_hash(checkout),
    })

    assert resolved.outcome == disputes.SELLER_FAVOR


async def test_a_missing_mandate_favours_the_buyer(session, verifier, token,
                                                   issuer):
    """The burden is on whoever took the money."""
    settlement = await charge(session, verifier, token, issuer)
    await session.commit()

    dispute = await disputes.open_dispute(session, settlement.payment_id)
    resolved = await disputes.adjudicate(session, dispute.dispute_id,
                                         {"intent_jwt": "signed-intent"})

    assert resolved.outcome == disputes.BUYER_FAVOR
    assert "no signed mandate presented" in resolved.evidence["findings"]


async def test_evidence_for_a_different_cart_favours_the_buyer(
        session, verifier, token, issuer):
    """Producing *a* cart is not producing *the* cart."""
    sd_jwt, _ = issuer.issue()
    settlement = await charge(session, verifier, token, issuer, sd_jwt=sd_jwt)
    await session.commit()

    dispute = await disputes.open_dispute(session, settlement.payment_id)
    resolved = await disputes.adjudicate(session, dispute.dispute_id, {
        "mandate_sd_jwt": sd_jwt,
        "intent_jwt": "signed-intent",
        "checkout_hash": ap2.checkout_hash(issuer.checkout("999.00")),
    })

    assert resolved.outcome == disputes.BUYER_FAVOR


async def test_an_escalated_purchase_needs_its_approval_receipt(
        session, verifier, token, issuer):
    sd_jwt, _ = issuer.issue()
    checkout = issuer.checkout()
    settlement = await charge(session, verifier, token, issuer, sd_jwt=sd_jwt,
                              checkout=checkout)
    await session.commit()

    dispute = await disputes.open_dispute(session, settlement.payment_id)
    resolved = await disputes.adjudicate(session, dispute.dispute_id, {
        "mandate_sd_jwt": sd_jwt,
        "intent_jwt": "signed-intent",
        "checkout_hash": ap2.checkout_hash(checkout),
        "escalated": True,          # a human was asked...
    })                              # ...but no receipt proves they answered

    assert resolved.outcome == disputes.BUYER_FAVOR


async def test_only_a_captured_payment_can_be_disputed(session):
    with pytest.raises(disputes.DisputeError):
        await disputes.open_dispute(session, "ynp_never_happened")
