"""T2 -- SD-JWT signing and verification (PLAN.md §10, gate G1).

"Valid passes; payload mutated by one byte, another issuer's signature, expired
exp, duplicate jti -> rejected."

Plus T3's key-binding half, which Dev 1 owns end to end but which is verified
here at the library level: a cloned agent without the `cnf` private key cannot
present a mandate.
"""

from __future__ import annotations

import time

import pytest

from trustlib import fake, sdjwt
from trustlib.jose import b64u_decode, b64u_encode, generate_ed25519, public_jwk

KID = "v1"
AUD = "https://merchant.aval.example"
NONCE = "nonce-from-the-merchant"


@pytest.fixture
def issuer():
    key = generate_ed25519()
    return key, {KID: key}


@pytest.fixture
def holder():
    return generate_ed25519()


@pytest.fixture
def claims(holder):
    return fake.mandate(agent_jwk=public_jwk(holder)).model_dump(mode="json")


# ==========================================================================
# Happy path
# ==========================================================================
def test_valid_mandate_verifies(issuer, claims):
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)

    verified = sdjwt.verify(token, jwks)

    assert verified["jti"] == claims["jti"]
    assert verified["sub"] == "usr_marta"
    assert verified["limits"]["max_per_txn"] == "150"


def test_selective_disclosure_hides_then_reveals(issuer, claims):
    key, jwks = issuer
    token = sdjwt.issue(
        claims,
        key,
        kid=KID,
        selective={"email": "marta@example.com", "shipping_address": "Bogota"},
    )

    # The issuer JWT alone must not carry the PII in the clear.
    issuer_jwt = token.split("~")[0]
    payload = b64u_decode(issuer_jwt.split(".")[1]).decode()
    assert "marta@example.com" not in payload
    assert "_sd" in payload

    # Presenting the disclosures reveals them.
    revealed = sdjwt.verify(token, jwks)
    assert revealed["email"] == "marta@example.com"
    assert revealed["shipping_address"] == "Bogota"

    # And presenting none keeps them hidden.
    withheld = sdjwt.verify(issuer_jwt + "~", jwks)
    assert "email" not in withheld


# ==========================================================================
# T2 -- the four rejections
# ==========================================================================
def test_one_byte_mutation_is_rejected(issuer, claims):
    """The demo moment: flip a byte, verification dies."""
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)

    header, payload_b64, signature = token.rstrip("~").split(".")
    payload = b64u_decode(payload_b64)
    mutated = payload.replace(b'"150"', b'"950"')
    assert mutated != payload, "fixture must contain the limit we mutate"
    tampered = f"{header}.{b64u_encode(mutated)}.{signature}~"

    with pytest.raises(sdjwt.InvalidSignature):
        sdjwt.verify(tampered, jwks)


def test_another_issuers_signature_is_rejected(issuer, claims):
    _, jwks = issuer
    impostor = generate_ed25519()
    token = sdjwt.issue(claims, impostor, kid=KID)

    with pytest.raises(sdjwt.InvalidSignature):
        sdjwt.verify(token, jwks)


def test_expired_mandate_is_rejected(issuer, claims):
    key, jwks = issuer
    past = int(time.time()) - 3600
    token = sdjwt.issue({**claims, "nbf": past - 60, "exp": past}, key, kid=KID)

    with pytest.raises(sdjwt.Expired):
        sdjwt.verify(token, jwks)


def test_not_yet_valid_mandate_is_rejected(issuer, claims):
    key, jwks = issuer
    future = int(time.time()) + 3600
    token = sdjwt.issue({**claims, "nbf": future, "exp": future + 60}, key, kid=KID)

    with pytest.raises(sdjwt.NotYetValid):
        sdjwt.verify(token, jwks)


def test_unknown_kid_is_rejected(issuer, claims):
    key, _ = issuer
    token = sdjwt.issue(claims, key, kid="v99")

    with pytest.raises(sdjwt.InvalidSignature):
        sdjwt.verify(token, {KID: key})


def test_rotation_keeps_previous_key_working(issuer, claims):
    """JWKS publishes current + previous with 24h grace (decision #6)."""
    old_key, _ = issuer
    new_key = generate_ed25519()
    signed_with_old = sdjwt.issue(claims, old_key, kid="v1")

    assert sdjwt.verify(signed_with_old, {"v1": old_key, "v2": new_key})


def test_forged_disclosure_is_rejected(issuer, claims):
    """A disclosure the issuer never signed must not be accepted."""
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID, selective={"email": "marta@example.com"})
    forged, _ = sdjwt.make_disclosure("email", "attacker@evil.example")
    issuer_jwt = token.split("~")[0]

    with pytest.raises(sdjwt.InvalidSignature):
        sdjwt.verify(f"{issuer_jwt}~{forged}~", jwks)


# ==========================================================================
# Key binding -- the anti-impersonation half
# ==========================================================================
def test_key_binding_proves_possession(issuer, holder, claims):
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)
    presented = sdjwt.attach_key_binding(token, holder, nonce=NONCE, aud=AUD)

    verified = sdjwt.verify(presented, jwks, nonce=NONCE, aud=AUD, require_key_binding=True)
    assert verified["jti"] == claims["jti"]


def test_cloned_agent_without_the_key_is_rejected(issuer, claims):
    """An impersonated agent dies at the signature, before any call to us."""
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)
    clone = generate_ed25519()  # same identity claimed, different key

    presented = sdjwt.attach_key_binding(token, clone, nonce=NONCE, aud=AUD)

    with pytest.raises(sdjwt.InvalidKeyBinding):
        sdjwt.verify(presented, jwks, nonce=NONCE, aud=AUD, require_key_binding=True)


def test_missing_key_binding_is_rejected_when_required(issuer, claims):
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)

    with pytest.raises(sdjwt.InvalidKeyBinding):
        sdjwt.verify(token, jwks, nonce=NONCE, aud=AUD, require_key_binding=True)


def test_replayed_key_binding_with_wrong_nonce_is_rejected(issuer, holder, claims):
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)
    presented = sdjwt.attach_key_binding(token, holder, nonce="an-old-nonce", aud=AUD)

    with pytest.raises(sdjwt.InvalidKeyBinding):
        sdjwt.verify(presented, jwks, nonce=NONCE, aud=AUD, require_key_binding=True)


def test_key_binding_for_another_audience_is_rejected(issuer, holder, claims):
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID)
    presented = sdjwt.attach_key_binding(
        token, holder, nonce=NONCE, aud="https://another-merchant.example"
    )

    with pytest.raises(sdjwt.InvalidKeyBinding):
        sdjwt.verify(presented, jwks, nonce=NONCE, aud=AUD, require_key_binding=True)


def test_key_binding_cannot_be_lifted_onto_other_disclosures(issuer, holder, claims):
    """sd_hash covers the presentation, so a KB-JWT is not transplantable."""
    key, jwks = issuer
    token = sdjwt.issue(claims, key, kid=KID, selective={"email": "marta@example.com"})
    presented = sdjwt.attach_key_binding(token, holder, nonce=NONCE, aud=AUD)

    issuer_jwt = presented.split("~")[0]
    kb_jwt = presented.split("~")[-1]
    stripped = f"{issuer_jwt}~{kb_jwt}"  # same KB, disclosures removed

    with pytest.raises(sdjwt.InvalidKeyBinding):
        sdjwt.verify(stripped, jwks, nonce=NONCE, aud=AUD, require_key_binding=True)
