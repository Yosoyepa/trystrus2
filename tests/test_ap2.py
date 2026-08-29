"""AP2 conformance (decision 0023).

Two things are being asserted here:

1. Our mandate really is an AP2 Open Payment Mandate -- the projection is
   derived from the native fields, so it cannot drift from what the gate reads.
2. The Checkout JWT obeys the specification's signature rule, which is the one
   place Aval may not use Ed25519.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trustlib import ap2, fake
from trustlib.jose import (
    generate_ed25519,
    generate_p256,
    peek_header,
    sign_compact,
    verify_compact,
)

MERCHANT_SITE = "https://merchant.aval.example"


# ==========================================================================
# Amount encoding at the AP2 boundary
# ==========================================================================
@pytest.mark.parametrize(
    ("decimal_amount", "minor"),
    [("130.00", 13000), ("0.01", 1), ("199.00", 19900), ("400", 40000)],
)
def test_minor_unit_round_trip(decimal_amount, minor):
    """AP2 uses integer minor units; our contract uses 2dp strings."""
    assert ap2.to_minor_units(decimal_amount) == minor
    assert Decimal(ap2.from_minor_units(minor)) == Decimal(decimal_amount)


def test_conversion_does_not_go_through_float():
    """0.1 + 0.2 style drift must not reach an amount."""
    assert ap2.to_minor_units("1234567.89") == 123456789


# ==========================================================================
# Open Payment Mandate projection
# ==========================================================================
def test_mandate_projects_to_ap2_open_payment_mandate():
    claims = ap2.apply_ap2_projection(fake.mandate())

    assert claims.vct == ap2.VCT_PAYMENT_OPEN
    kinds = {c["type"] for c in claims.constraints}
    assert kinds == {
        ap2.C_AMOUNT_RANGE,
        ap2.C_BUDGET,
        ap2.C_ALLOWED_PAYEES,
        ap2.C_AGENT_RECURRENCE,
        ap2.C_EXECUTION_DATE,
    }


def test_constraints_carry_the_same_numbers_as_limits():
    """The projection must never become a second source of truth."""
    claims = ap2.apply_ap2_projection(
        fake.mandate(max_per_txn="150", total_budget="400", max_txn_count=3))
    by_type = {c["type"]: c for c in claims.constraints}

    assert by_type[ap2.C_AMOUNT_RANGE]["max"] == ap2.to_minor_units("150")
    assert by_type[ap2.C_BUDGET]["max"] == ap2.to_minor_units("400")
    assert by_type[ap2.C_AGENT_RECURRENCE]["max_occurrences"] == 3
    assert by_type[ap2.C_AGENT_RECURRENCE]["frequency"] == "MONTHLY"
    assert by_type[ap2.C_ALLOWED_PAYEES]["allowed"] == [{"id": "vuelaya"}]


def test_projection_leaves_native_contract_fields_untouched():
    """Dev 1 and Dev 2 read schemas.md §1; they must not be disturbed."""
    original = fake.mandate()
    projected = ap2.apply_ap2_projection(original)

    assert projected.limits == original.limits
    assert projected.scope == original.scope
    assert projected.validity == original.validity
    assert projected.cnf == original.cnf
    assert projected.conditions == original.conditions


# ==========================================================================
# Checkout JWT -- the signature rule
# ==========================================================================
@pytest.fixture
def checkout_payload():
    return ap2.build_checkout_payload(
        order_id="ord_01J8Z",
        merchant_id="vuelaya",
        merchant_name="VuelaYa",
        merchant_website=MERCHANT_SITE,
        line_items=[{"id": "ofr_COR_130", "label": "BOG->COR", "amount": 13000}],
        total_price="130.00",
        currency="USD",
    )


def test_checkout_jwt_is_signed_with_es256(checkout_payload):
    """AP2: the Checkout JWT MUST NOT use a deterministic signature."""
    key = generate_p256()
    token = sign_compact(checkout_payload, key, kid="m1", typ="JWT")

    assert peek_header(token)["alg"] == "ES256"
    assert verify_compact(token, key)["order_id"] == "ord_01J8Z"


def test_es256_is_non_deterministic_and_ed25519_is_not(checkout_payload):
    """The empirical reason the spec forbids Ed25519 for hash-bound checkouts.

    A deterministic signature over a low-entropy cart is precomputable, which
    is the rainbow-table attack the specification calls out by name.
    """
    p256, ed = generate_p256(), generate_ed25519()

    assert sign_compact(checkout_payload, p256) != sign_compact(checkout_payload, p256)
    assert sign_compact(checkout_payload, ed) == sign_compact(checkout_payload, ed)


def test_checkout_hash_binds_the_cart(checkout_payload):
    key = generate_p256()
    token = sign_compact(checkout_payload, key, kid="m1", typ="JWT")

    assert ap2.verify_checkout_binding(
        checkout_jwt=token, claimed_hash=ap2.checkout_hash(token))


def test_a_swapped_cart_breaks_the_binding(checkout_payload):
    """Re-sign the same cart at a different price: the old hash must not match."""
    key = generate_p256()
    honest = sign_compact(checkout_payload, key, kid="m1", typ="JWT")

    swapped_payload = {**checkout_payload, "total_price": ap2.to_minor_units("30.00")}
    swapped = sign_compact(swapped_payload, key, kid="m1", typ="JWT")

    assert not ap2.verify_checkout_binding(
        checkout_jwt=swapped, claimed_hash=ap2.checkout_hash(honest))


def test_closed_payment_mandate_references_the_checkout(checkout_payload):
    key = generate_p256()
    token = sign_compact(checkout_payload, key, kid="m1", typ="JWT")

    mandate = ap2.closed_payment_mandate(
        transaction_id="ynp_01J8Z",
        payee_id="vuelaya",
        payee_name="VuelaYa",
        amount="130.00",
        currency="USD",
        payment_instrument_id="ynt_abc",
        bound_checkout_jwt=token,
    )

    assert mandate["vct"] == ap2.VCT_PAYMENT_CLOSED
    assert mandate["payment_amount"] == {"amount": 13000, "currency": "USD"}
    reference = mandate["constraints"][0]
    assert reference["conditional_transaction_id"] == ap2.checkout_hash(token)


def test_closed_checkout_mandate_shape(checkout_payload):
    key = generate_p256()
    token = sign_compact(checkout_payload, key, kid="m1", typ="JWT")

    mandate = ap2.closed_checkout_mandate(token)

    assert mandate["vct"] == ap2.VCT_CHECKOUT_CLOSED
    assert mandate["_sd_alg"] == "sha-256"
    assert mandate["checkout_hash"] == ap2.checkout_hash(token)
