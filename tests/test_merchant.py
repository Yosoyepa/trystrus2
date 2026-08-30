"""VuelaYa catalogue, Checkout JWT and fail-closed charge-path tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from merchant import catalog
from merchant.charge import ChargeRefused, ChargeService
from merchant.checkout_jwt import CheckoutJWTService
from merchant.config import Settings as MerchantSettings
from merchant.schemas import ChargeRequest
from trustlib import ap2, fake, sdjwt
from trustlib.jose import generate_ed25519, generate_p256, public_jwk, sign_detached
from trustlib.models import Decision, DecisionOutcome, ReasonCode, Receipt

pytestmark = pytest.mark.asyncio


class StubJWKS:
    def __init__(self, keys):
        self._keys = keys

    async def keys(self):
        return self._keys


class StubVerify:
    def __init__(self, decision: Decision):
        self.decision = decision
        self.calls: list[dict] = []

    async def verify(self, **kwargs):
        self.calls.append(kwargs)
        return self.decision


class RailSpy:
    def __init__(self):
        self.calls: list[dict] = []

    async def capture(self, **kwargs):
        self.calls.append(kwargs)
        return Receipt(
            purchase_id=kwargs["purchase_id"],
            capture_id="ynp_test_capture",
            amount=f"{kwargs['amount']:.2f}",
            currency=kwargs["currency"],
            captured_at=datetime.now(UTC),
            mandate_jti="mdt_signed",
            simulated=True,
        )


async def _prepared_charge(session, tmp_path, *, verify_decision=None,
                           checkout_hash: str | None = None):
    config = MerchantSettings(secrets_dir=tmp_path)
    await catalog.seed_initial_offers(session, config)
    offer = await catalog.get_offer(session, "ofr_COR_130")
    assert offer is not None

    merchant_key = generate_p256()
    checkout = CheckoutJWTService(config=config, signing_key=merchant_key)
    quote = await checkout.quote(session, offer)

    agent_key = generate_ed25519()
    issuer_key = generate_ed25519()
    mandate = ap2.apply_ap2_projection(fake.mandate(
        jti="mdt_signed", agent_jwk=public_jwk(agent_key),
        payment_method_ref="ynt_live", conditions=None))
    mandate_sd_jwt = sdjwt.issue(
        mandate.model_dump(mode="json", exclude_none=True), issuer_key, kid="v1")
    intent = fake.intent(
        mandate_jti=mandate.jti, offer_id=offer.offer_id, amount=offer.amount,
        checkout_hash=checkout_hash if checkout_hash is not None else quote.checkout_hash)
    intent_jwt = sign_detached(intent.model_dump(mode="json"), agent_key)
    body = ChargeRequest(
        purchase_id="pur_test",
        mandate_id="mdt_internal",
        mandate_sd_jwt=mandate_sd_jwt,
        intent=intent,
        intent_jwt=intent_jwt,
        checkout_jwt=quote.checkout_jwt,
        payment_method_ref="ynt_live",
        amount=offer.amount,
        currency=offer.currency,
        idempotency_key="merchant-charge-test",
    )
    verifier = StubVerify(verify_decision or Decision(
        decision=DecisionOutcome.APPROVED, reservation_id="rsv_test"))
    rail = RailSpy()
    service = ChargeService(
        jwks=StubJWKS({"v1": issuer_key}), verify_client=verifier,
        checkout=checkout, rail=rail, merchant_id="vuelaya")
    return service, body, verifier, rail, offer


async def test_catalogue_seeds_filters_and_price_changes_persist(session, tmp_path):
    config = MerchantSettings(secrets_dir=tmp_path)
    assert await catalog.seed_initial_offers(session, config) == 4

    filtered = await catalog.list_offers(
        session, origin="bog", destination="COR",
        travel_date=__import__("datetime").date(2026, 8, 30))
    assert [offer.offer_id for offer in filtered] == ["ofr_COR_130"]

    changed = await catalog.update_price(session, "ofr_COR_130", Decimal("99.00"))
    assert changed and changed.amount == "99.00"
    await session.commit()

    # Fixture loading is safe on every service start: it does not reset a
    # judge-triggered price mutation.
    assert await catalog.seed_initial_offers(session, config) == 0
    assert (await catalog.get_offer(session, "ofr_COR_130")).amount == "99.00"


async def test_checkout_jwt_is_persisted_and_charge_captures_only_after_verify(
        session, tmp_path):
    service, body, verifier, rail, offer = await _prepared_charge(session, tmp_path)

    receipt = await service.charge(session, body)

    assert receipt.purchase_id == "pur_test"
    assert receipt.capture_id == "ynp_test_capture"
    assert verifier.calls == [{
        "mandate_id": "mdt_internal", "intent_jwt": body.intent_jwt,
        "idempotency_key": "merchant-charge-test", "agent_id": body.intent.agent,
    }]
    assert len(rail.calls) == 1
    assert rail.calls[0]["amount"] == offer.amount_decimal


async def test_checkout_replay_returns_its_stored_receipt_without_a_second_capture(
        session, tmp_path):
    service, body, verifier, rail, _ = await _prepared_charge(session, tmp_path)

    first = await service.charge(session, body)
    replay = await service.charge(session, body)

    assert replay == first
    assert len(verifier.calls) == 1
    assert len(rail.calls) == 1

    with pytest.raises(ChargeRefused):
        await service.charge(
            session, body.model_copy(update={"purchase_id": "pur_other"}))
    assert len(rail.calls) == 1


async def test_verify_rejection_never_calls_the_rail(session, tmp_path):
    service, body, _, rail, _ = await _prepared_charge(
        session, tmp_path,
        verify_decision=Decision(decision=DecisionOutcome.REJECTED,
                                 reason_code=ReasonCode.MANDATE_REVOKED),
    )

    with pytest.raises(ChargeRefused) as refused:
        await service.charge(session, body)

    assert refused.value.reason_code is ReasonCode.MANDATE_REVOKED
    assert rail.calls == []  # The M2 exit criterion: a real spy, not code reading.


async def test_bad_checkout_binding_never_reaches_verify_or_rail(session, tmp_path):
    service, body, verifier, rail, _ = await _prepared_charge(
        session, tmp_path, checkout_hash="not-the-merchant-cart")

    with pytest.raises(ChargeRefused) as refused:
        await service.charge(session, body)

    assert refused.value.reason_code is ReasonCode.CONDITION_FAILED
    assert verifier.calls == []
    assert rail.calls == []


async def test_invalid_agent_signature_never_reaches_the_rail(session, tmp_path):
    service, body, verifier, rail, _ = await _prepared_charge(session, tmp_path)
    header, _, signature = body.intent_jwt.split(".")
    forged_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    body = body.model_copy(update={"intent_jwt": f"{header}..{forged_signature}"})

    with pytest.raises(ChargeRefused) as refused:
        await service.charge(session, body)

    assert refused.value.reason_code is ReasonCode.INVALID_PROOF_OF_POSSESSION
    assert verifier.calls == []
    assert rail.calls == []
