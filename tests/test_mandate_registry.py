"""The mandate registry: issuance, JWKS, and sticky derivation.

Gate G1's exit criterion is "create and verify a mandate; mutate one byte ->
rejected; KB-JWT with nonce; signed root verifiable". The crypto half lives in
test_sdjwt.py; this file covers the service that wraps it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from api.config import Settings
from api.services.keys import KeyStore
from api.services.mandate_registry import MandateRegistry
from trustlib import ap2, sdjwt
from trustlib.jose import generate_ed25519, public_jwk
from trustlib.models import (
    MandateClaimsInput,
    MandateLimits,
    MandateScope,
    MandateValidity,
    MaxTxn,
    Period,
)


@pytest.fixture
def registry(tmp_path):
    """A registry with throwaway keys, so tests never touch ./secrets."""
    config = Settings(secrets_dir=tmp_path, gcp_project=None)
    return MandateRegistry(KeyStore(config), config)


@pytest.fixture
def request_body():
    now = datetime.now(UTC)
    return MandateClaimsInput(
        user_id="usr_marta",
        agent_id="agt_flights",
        currency="USD",
        scope=MandateScope(categories=["flights"], merchants=["vuelaya"]),
        limits=MandateLimits(
            max_per_txn=Decimal("150"),
            total_budget=Decimal("400"),
            max_txn=MaxTxn(count=3, period=Period.MONTH),
        ),
        validity=MandateValidity(
            not_before=now - timedelta(minutes=1), expires_at=now + timedelta(days=30)
        ),
        conditions={"<": [{"var": "offer.price"}, 150]},
        payment_method_ref="ynt_test",
    )


# ==========================================================================
# Issuance
# ==========================================================================
def test_issued_mandate_verifies_against_its_own_jwks(registry, request_body):
    """The full loop: issue, publish, verify -- as a merchant would."""
    issued = registry.issue(request_body)

    verified = registry.verify(issued.sd_jwt)

    assert verified.jti == issued.jti
    assert verified.sub == "usr_marta"
    assert verified.limits.max_per_txn == Decimal("150")


def test_a_stranger_can_verify_with_the_jwks_alone(registry, request_body):
    """Decision #6: the merchant does not have to trust our answer.

    Verification here uses only the published JWKS -- no registry, no call
    back to us. That is what makes the check meaningful to a third party.
    """
    issued = registry.issue(request_body)
    published = registry.jwks()

    from trustlib.jose import jwk_from_dict

    keys = {k["kid"]: jwk_from_dict(k) for k in published["keys"]}

    assert sdjwt.verify(issued.sd_jwt, keys)["jti"] == issued.jti


def test_issued_mandates_carry_ap2_shape(registry, request_body):
    issued = registry.issue(request_body)

    assert issued.claims.vct == ap2.VCT_PAYMENT_OPEN
    kinds = {c["type"] for c in issued.claims.constraints}
    assert ap2.C_AMOUNT_RANGE in kinds
    assert ap2.C_BUDGET in kinds


def test_the_agents_key_is_bound_into_the_mandate(registry, request_body):
    """`cnf.jwk` is what makes impersonation fail at the signature."""
    agent = generate_ed25519()
    issued = registry.issue(request_body.model_copy(update={"agent_jwk": public_jwk(agent)}))

    assert issued.claims.cnf.jwk["x"] == public_jwk(agent)["x"]


def test_jwks_publishes_both_curves(registry):
    """EdDSA for mandates, ES256 for AP2 Checkout JWTs (decision 0023)."""
    keys = {k["kid"]: k for k in registry.jwks()["keys"]}

    assert keys["v1"]["alg"] == "EdDSA"
    assert keys["m1"]["alg"] == "ES256"
    assert all(k["use"] == "sig" for k in keys.values())


def test_jwks_never_leaks_a_private_key(registry):
    """The obvious catastrophe, asserted rather than assumed."""
    for key in registry.jwks()["keys"]:
        assert "d" not in key, "private component published in JWKS"


# ==========================================================================
# Claims are built before the ceremony, signed after
# ==========================================================================
def test_build_does_not_sign(registry, request_body):
    """A signed mandate must not exist before the human agrees.

    The passkey challenge is the hash of these claims, so they have to be
    final before the gesture -- but signing them first would produce a valid
    mandate nobody authorized.
    """
    claims = registry.build_claims(request_body)

    assert claims.jti.startswith("mdt_")
    assert claims.vct == ap2.VCT_PAYMENT_OPEN

    issued = registry.sign(claims)
    assert issued.claims.jti == claims.jti


def test_selective_claims_are_withheld_until_disclosed(registry, request_body):
    claims = registry.build_claims(request_body)
    issued = registry.sign(claims, disclose={"email": "marta@example.com"})

    issuer_jwt = issued.sd_jwt.split("~")[0]
    withheld = registry.verify(issuer_jwt + "~")
    revealed = registry.verify(issued.sd_jwt)

    assert not hasattr(withheld, "email") or "email" not in withheld.model_dump()
    assert revealed is not None
    assert "marta@example.com" not in issuer_jwt


def test_unknown_selective_claims_are_ignored(registry, request_body):
    """Only `email` and `shipping_address` are disclosable (schemas.md §1)."""
    claims = registry.build_claims(request_body)
    issued = registry.sign(claims, disclose={"limits": "tampered"})

    assert registry.verify(issued.sd_jwt).limits.max_per_txn == Decimal("150")


# ==========================================================================
# Sticky mini-mandates (schemas.md §5.3)
# ==========================================================================
def test_derived_mandate_links_to_its_parent(registry, request_body):
    parent = registry.build_claims(request_body)

    child = registry.derive(
        parent, limits=MandateLimits(max_per_txn=Decimal("300"), total_budget=Decimal("300"))
    )

    assert child.parent_jti == parent.jti
    assert child.jti != parent.jti
    assert child.cnf == parent.cnf  # same agent, new authority


def test_a_derived_mandate_never_outlives_its_parent(registry, request_body):
    """Approving an escalation must not extend the delegation in time."""
    parent = registry.build_claims(request_body)
    near_expiry = parent.model_copy(update={"exp": int(time.time()) + 60})

    child = registry.derive(near_expiry, limits=near_expiry.limits, ttl_seconds=86400)

    assert child.exp <= near_expiry.exp


def test_derived_mandate_is_independently_verifiable(registry, request_body):
    parent = registry.build_claims(request_body)
    child = registry.derive(
        parent, limits=MandateLimits(max_per_txn=Decimal("300"), total_budget=Decimal("300"))
    )

    issued = registry.sign(child)
    verified = registry.verify(issued.sd_jwt)

    assert verified.parent_jti == parent.jti
    assert verified.limits.max_per_txn == Decimal("300")
