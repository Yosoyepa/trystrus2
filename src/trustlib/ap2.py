"""AP2 (Agent Payments Protocol) conformance layer.

Aval is AP2-aligned by decision #2 / ADR-001. This module is where that stops
being a claim and becomes machine-checkable.

What AP2 actually specifies today (verified Aug 2026 against
https://ap2-protocol.org/ap2/specification/ -- the spec moved on from the
Sept 2025 "Intent -> Cart -> Payment" framing our ADR-001 still cites):

* Two mandate families, **Checkout** and **Payment**, each in an **open**
  variant (reusable, carries `constraints[]` and `cnf`) and a **closed**
  variant (one transaction), encoded as SD-JWT with a `vct` type claim.
* The merchant signs a **Checkout JWT**; the payment is bound to it by
  `checkout_hash`, so the cart cannot be swapped after approval.
* Roles: Shopping Agent, Credential Provider, Merchant, Merchant Payment
  Processor, and a non-agentic **Trusted Surface** that obtains user consent.

How Aval maps onto it:

| AP2                              | Aval                                    |
|----------------------------------|-----------------------------------------|
| Open Payment Mandate             | our mandate SD-JWT (limits/scope/cnf)   |
| Trusted Surface                  | the passkey ceremony (decision #3)      |
| Checkout JWT + closed Checkout   | `build_checkout_jwt` below (merchant)   |
| Closed Payment Mandate           | `PurchaseIntent` (schemas.md §2)        |
| Credential Provider + MPP        | the Yuno-style orchestrator (simulated) |

The native fields of schemas.md §1 are never rewritten: `constraints` is an
*additive projection*, so Dev 1 and Dev 2 are unaffected.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from .canonical import canonical_json
from .jose import b64u_encode
from .models import MandateClaims

# --- vct values (AP2 credential types) ------------------------------------
VCT_PAYMENT_OPEN = "mandate.payment.open.1"
VCT_PAYMENT_CLOSED = "mandate.payment.1"
VCT_CHECKOUT_OPEN = "mandate.checkout.open.1"
VCT_CHECKOUT_CLOSED = "mandate.checkout.1"

# --- constraint types -----------------------------------------------------
C_AMOUNT_RANGE = "payment.amount_range"
C_BUDGET = "payment.budget"
C_ALLOWED_PAYEES = "payment.allowed_payees"
C_AGENT_RECURRENCE = "payment.agent_recurrence"
C_EXECUTION_DATE = "payment.execution_date"

_AP2_FREQUENCY = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY"}


# ==========================================================================
# Amount encoding
# ==========================================================================
# AP2 carries amounts as integers in minor units (19900 == $199.00); our
# frozen contract uses a fixed 2-decimal string ("130.00"). We convert at the
# boundary rather than touching the contract -- Decimal in between, no floats,
# no drift.
def to_minor_units(amount: str | Decimal, *, exponent: int = 2) -> int:
    return int((Decimal(amount) * (10**exponent)).to_integral_value())


def from_minor_units(minor: int, *, exponent: int = 2) -> str:
    return str((Decimal(minor) / (10**exponent)).quantize(Decimal("0.01")))


# ==========================================================================
# Open Payment Mandate: our limits, expressed as AP2 constraints
# ==========================================================================
def constraints_for(claims: MandateClaims) -> list[dict[str, Any]]:
    """Project a MandateClaims onto AP2 `constraints[]`.

    Purely derived from the native fields -- never a second source of truth.
    The gate keeps reading `limits`/`scope`/`validity`; this exists so an AP2
    verifier can read the same permission without knowing our schema.
    """
    out: list[dict[str, Any]] = []
    limits, scope, validity = claims.limits, claims.scope, claims.validity

    if limits.max_per_txn is not None:
        out.append(
            {
                "type": C_AMOUNT_RANGE,
                "currency": claims.currency,
                "min": 0,
                "max": to_minor_units(limits.max_per_txn),
            }
        )

    if limits.total_budget is not None:
        out.append(
            {
                "type": C_BUDGET,
                "currency": claims.currency,
                "max": to_minor_units(limits.total_budget),
            }
        )

    if scope.merchants:
        out.append(
            {
                "type": C_ALLOWED_PAYEES,
                "allowed": [{"id": m} for m in scope.merchants],
            }
        )

    if limits.max_txn is not None:
        out.append(
            {
                "type": C_AGENT_RECURRENCE,
                "frequency": _AP2_FREQUENCY[limits.max_txn.period.value],
                "max_occurrences": limits.max_txn.count,
            }
        )

    out.append(
        {
            "type": C_EXECUTION_DATE,
            "not_before": validity.not_before.isoformat(),
            "not_after": validity.expires_at.isoformat(),
        }
    )
    return out


def apply_ap2_projection(claims: MandateClaims) -> MandateClaims:
    """Return `claims` with `vct` and `constraints` filled in."""
    return claims.model_copy(
        update={
            "vct": VCT_PAYMENT_OPEN,
            "constraints": constraints_for(claims),
        }
    )


# ==========================================================================
# Checkout JWT (merchant-signed) and its hash binding
# ==========================================================================
def build_checkout_payload(
    *,
    order_id: str,
    merchant_id: str,
    merchant_name: str,
    merchant_website: str,
    line_items: list[dict[str, Any]],
    total_price: str | Decimal,
    currency: str,
    shipping_policy: str = "No physical shipment; e-ticket issued on capture.",
    return_policy: str = "Refundable up to 24h before departure.",
) -> dict[str, Any]:
    """The payload the merchant signs with **ES256** (never Ed25519).

    AP2, verbatim: "To prevent rainbow table attacks, the Checkout JWT MUST be
    signed using a digital signature scheme (e.g., ECDSA) and not a
    deterministic signature (e.g., Ed25519)."
    """
    return {
        "order_id": order_id,
        "merchant": {
            "id": merchant_id,
            "name": merchant_name,
            "website": merchant_website,
        },
        "line_items": line_items,
        "total_price": to_minor_units(total_price),
        "currency": currency,
        "shipping_policy": shipping_policy,
        "return_policy": return_policy,
    }


def checkout_hash(checkout_jwt: str) -> str:
    """`base64url(sha-256(checkout_jwt))` -- binds a payment to one cart.

    Comparing amounts field by field can be fooled by anything the comparison
    forgot to look at; a hash over the merchant's own signed bytes cannot.
    """
    return b64u_encode(hashlib.sha256(checkout_jwt.encode("ascii")).digest())


def closed_checkout_mandate(checkout_jwt: str) -> dict[str, Any]:
    """Closed Checkout Mandate (`mandate.checkout.1`) bound to a Checkout JWT."""
    return {
        "vct": VCT_CHECKOUT_CLOSED,
        "checkout_hash": checkout_hash(checkout_jwt),
        "_sd_alg": "sha-256",
    }


def closed_payment_mandate(
    *,
    transaction_id: str,
    payee_id: str,
    payee_name: str,
    amount: str | Decimal,
    currency: str,
    payment_instrument_id: str,
    bound_checkout_jwt: str | None = None,
) -> dict[str, Any]:
    """Closed Payment Mandate (`mandate.payment.1`).

    When `bound_checkout_jwt` is given, the mandate carries the reference
    constraint that ties this payment to that exact cart.
    """
    mandate: dict[str, Any] = {
        "vct": VCT_PAYMENT_CLOSED,
        "transaction_id": transaction_id,
        "payee": {"id": payee_id, "name": payee_name},
        "payment_amount": {
            "amount": to_minor_units(amount),
            "currency": currency,
        },
        "payment_instrument": {"id": payment_instrument_id, "type": "vaulted_token"},
    }
    if bound_checkout_jwt is not None:
        mandate["constraints"] = [
            {
                "type": "payment.reference",
                "conditional_transaction_id": checkout_hash(bound_checkout_jwt),
            }
        ]
    return mandate


def verify_checkout_binding(*, checkout_jwt: str, claimed_hash: str) -> bool:
    """Constant-shape check that a claimed `checkout_hash` matches the cart."""
    import hmac

    return hmac.compare_digest(checkout_hash(checkout_jwt), claimed_hash)


__all__ = [
    "VCT_PAYMENT_OPEN",
    "VCT_PAYMENT_CLOSED",
    "VCT_CHECKOUT_OPEN",
    "VCT_CHECKOUT_CLOSED",
    "to_minor_units",
    "from_minor_units",
    "constraints_for",
    "apply_ap2_projection",
    "build_checkout_payload",
    "checkout_hash",
    "closed_checkout_mandate",
    "closed_payment_mandate",
    "verify_checkout_binding",
    "canonical_json",
]
