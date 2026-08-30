"""The canonical fixtures are consumable by every workstream.

M0's exit criterion (PLAN-PARALELO §5) is that a test actually consumes the
fixtures and approves/rejects them correctly. These are the Python half.

They also protect against the quiet failure mode: someone regenerates the
fixtures, a signature stops matching, and Dev 1 or Dev 2 spends an hour
debugging their own correct code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trustlib import ap2, sdjwt
from trustlib.jose import (
    key_from_pem,
    jwk_from_dict,
    peek_header,
    verify_compact,
    verify_detached,
)

FIXTURES = Path(__file__).resolve().parents[1] / "aval" / "contracts" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def jwks() -> dict:
    """The issuer JWKS as `kid -> JWK`, exactly as a verifier would build it."""
    return {k["kid"]: jwk_from_dict(k) for k in load("issuer_jwks.json")["keys"]}


@pytest.fixture(scope="module")
def mandate() -> dict:
    return load("mandate_vuelaya.json")


# ==========================================================================
# The mandate
# ==========================================================================
def test_canonical_mandate_verifies_against_the_published_jwks(jwks, mandate):
    """What Dev 2 does at verify time, with nothing but the fixtures."""
    claims = sdjwt.verify(mandate["sd_jwt"], jwks)

    assert claims["jti"] == "mdt_fixture_vuelaya"
    assert claims["sub"] == "usr_marta"
    assert claims["limits"]["max_per_txn"] == "150"
    assert claims["limits"]["total_budget"] == "400"
    assert claims["limits"]["max_txn"] == {"count": 3, "period": "month"}
    assert claims["scope"]["merchants"] == ["vuelaya"]


def test_canonical_mandate_is_an_ap2_open_payment_mandate(jwks, mandate):
    claims = sdjwt.verify(mandate["sd_jwt"], jwks)

    assert claims["vct"] == ap2.VCT_PAYMENT_OPEN
    by_type = {c["type"]: c for c in claims["constraints"]}
    assert by_type[ap2.C_AMOUNT_RANGE]["max"] == 15000
    assert by_type[ap2.C_BUDGET]["max"] == 40000


def test_mandate_hides_pii_until_disclosed(jwks, mandate):
    """Selective disclosure: the issuer JWT must not leak email or address."""
    issuer_jwt = mandate["sd_jwt"].split("~")[0]

    withheld = sdjwt.verify(issuer_jwt + "~", jwks)
    assert "email" not in withheld
    assert "shipping_address" not in withheld

    revealed = sdjwt.verify(mandate["sd_jwt"], jwks)
    assert revealed["email"] == "marta@example.com"


# ==========================================================================
# The intents
# ==========================================================================
@pytest.mark.parametrize("name", [
    "intent_130_approved",
    "intent_300_escalated",
    "intent_wrong_category",
])
def test_honest_intents_verify_against_the_mandates_cnf_key(name, jwks, mandate):
    """These three differ in policy, not in cryptography -- all signatures valid.

    The distinction matters: the gate rejects them for *business* reasons, and
    a mock that conflates "bad signature" with "over limit" would hide bugs.
    """
    claims = sdjwt.verify(mandate["sd_jwt"], jwks)
    agent_key = jwk_from_dict(claims["cnf"]["jwk"])
    fixture = load(f"{name}.json")

    payload = verify_detached(fixture["intent_jwt"], fixture["intent"], agent_key)
    assert payload["mandate_jti"] == claims["jti"]


def test_impersonation_fixture_fails_against_the_bound_key(jwks, mandate):
    """The cloned-agent case: right claims, wrong key, no purchase."""
    claims = sdjwt.verify(mandate["sd_jwt"], jwks)
    agent_key = jwk_from_dict(claims["cnf"]["jwk"])
    fixture = load("intent_wrong_key.json")

    with pytest.raises(Exception):
        verify_detached(fixture["intent_jwt"], fixture["intent"], agent_key)

    assert fixture["expect"]["reason_code"] == "INVALID_PROOF_OF_POSSESSION"


def test_intents_respect_the_120_second_freshness_window():
    """schemas.md §2: exp - iat <= 120s."""
    for name in ("intent_130_approved", "intent_300_escalated",
                 "intent_wrong_category", "intent_wrong_key"):
        intent = load(f"{name}.json")["intent"]
        assert intent["exp"] - intent["iat"] <= 120, name


def test_intent_amounts_match_the_offers_they_reference():
    """The anti-price-manipulation invariant, checked at the fixture level."""
    offers = {o["offer_id"]: o for o in load("offers.json")}

    for name in ("intent_130_approved", "intent_300_escalated",
                 "intent_wrong_category"):
        intent = load(f"{name}.json")["intent"]
        assert intent["amount"] == offers[intent["offer_id"]]["amount"], name


# ==========================================================================
# The AP2 Checkout JWT
# ==========================================================================
def test_checkout_jwt_is_es256_and_verifies(jwks):
    fixture = load("checkout_jwt.json")

    assert peek_header(fixture["checkout_jwt"])["alg"] == "ES256"
    payload = verify_compact(fixture["checkout_jwt"], jwks["m1"])
    assert payload["total_price"] == 13000
    assert payload["merchant"]["id"] == "vuelaya"


def test_checkout_hash_binds_the_fixture_cart():
    fixture = load("checkout_jwt.json")

    assert ap2.verify_checkout_binding(
        checkout_jwt=fixture["checkout_jwt"],
        claimed_hash=fixture["checkout_hash"],
    )
    assert fixture["closed_checkout_mandate"]["vct"] == ap2.VCT_CHECKOUT_CLOSED


def test_checkout_total_matches_the_offer_it_sells():
    fixture = load("checkout_jwt.json")
    offers = {o["offer_id"]: o for o in load("offers.json")}
    line = fixture["payload"]["line_items"][0]

    assert line["amount"] == ap2.to_minor_units(offers[line["id"]]["amount"])


# ==========================================================================
# Keys
# ==========================================================================
def test_private_pems_load_and_match_their_published_public_halves():
    """A fixture whose private and public halves disagree is a silent trap."""
    for name in ("issuer_key", "agent_key", "merchant_es256_key"):
        fixture = load(f"{name}.json")
        key = key_from_pem(fixture["private_pem"].encode())
        published = fixture["public_jwk"]

        assert json.loads(key.export_public())["x"] == published["x"], name
