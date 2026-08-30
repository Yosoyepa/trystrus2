#!/usr/bin/env python
"""Generate the canonical fixtures of schemas.md §9.

Run: `uv run python aval/contracts/fixtures/build_fixtures.py`

These are **community property** (PLAN-PARALELO §4): Dev 1 signs intents with
`agent_key.json`, Dev 2 verifies mandates against `issuer_jwks.json`, Dev 3
issues them. Regenerating changes every signature, so it is a deliberate act,
not something a test does on the fly.

The private keys here are test material and are committed on purpose — they are
the only way another workstream can produce a valid signature without running
our service. Real issuing keys live in Secret Manager (decision #15).
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent          # aval/contracts/fixtures
REPO_ROOT = HERE.parents[2]                      # aval/contracts -> aval -> root
sys.path.insert(0, str(REPO_ROOT / "src"))

from trustlib import ap2, fake, sdjwt  # noqa: E402
from trustlib.jose import (  # noqa: E402
    generate_ed25519,
    generate_p256,
    key_to_pem,
    public_jwk,
    sign_compact,
    sign_detached,
)

ISSUER_KID = "v1"
MERCHANT_KID = "m1"
MERCHANT_SITE = "https://merchant.aval.example"


def write(name: str, data) -> None:
    path = HERE / name
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    path.write_text(text)
    print(f"  {name}")


def main() -> None:
    print("fixtures (schemas.md §9):")

    # ---- keys ------------------------------------------------------------
    issuer_key = generate_ed25519()
    agent_key = generate_ed25519()
    wrong_key = generate_ed25519()          # signs the impersonation fixture
    merchant_key = generate_p256()          # ES256, AP2 Checkout JWT only

    write("issuer_key.json", {
        "kid": ISSUER_KID,
        "alg": "EdDSA",
        "private_pem": key_to_pem(issuer_key).decode(),
        "public_jwk": public_jwk(issuer_key, kid=ISSUER_KID),
        "note": "Test issuer. Real keys live in Secret Manager (decision #15).",
    })
    write("agent_key.json", {
        "agent_id": fake.DEFAULT_AGENT,
        "alg": "EdDSA",
        "private_pem": key_to_pem(agent_key).decode(),
        "public_jwk": public_jwk(agent_key),
        "note": "Dev 1 signs purchase intents with this (schemas.md §2).",
    })
    write("wrong_agent_key.json", {
        "alg": "EdDSA",
        "private_pem": key_to_pem(wrong_key).decode(),
        "public_jwk": public_jwk(wrong_key),
        "note": "Not bound by any mandate -> INVALID_PROOF_OF_POSSESSION.",
    })
    write("merchant_es256_key.json", {
        "kid": MERCHANT_KID,
        "alg": "ES256",
        "private_pem": key_to_pem(merchant_key).decode(),
        "public_jwk": public_jwk(merchant_key, kid=MERCHANT_KID),
        "note": "AP2 forbids Ed25519 for the Checkout JWT (decision 0023).",
    })
    write("issuer_jwks.json", {
        "keys": [
            public_jwk(issuer_key, kid=ISSUER_KID),
            public_jwk(merchant_key, kid=MERCHANT_KID),
        ]
    })

    # ---- the canonical mandate: <$150, 3/month, USD 400 ------------------
    claims = ap2.apply_ap2_projection(
        fake.mandate(
            jti="mdt_fixture_vuelaya",
            agent_jwk=public_jwk(agent_key),
            max_per_txn="150",
            total_budget="400",
            max_txn_count=3,
        )
    )
    payload = claims.model_dump(mode="json", exclude_none=True)
    sd_jwt = sdjwt.issue(
        payload, issuer_key, kid=ISSUER_KID,
        selective={"email": "marta@example.com", "shipping_address": "Bogota, CO"},
    )
    write("mandate_vuelaya.json", {
        "claims": payload,
        "sd_jwt": sd_jwt,
        "note": "Marta lets agt_flights buy flights from vuelaya: "
                "<$150 per txn, 3 per month, USD 400 total.",
    })

    # ---- offers ---------------------------------------------------------
    offers = [
        fake.offer(offer_id="ofr_COR_130", amount="130.00",
                   title="BOG->COR morning flight"),
        fake.offer(offer_id="ofr_MDE_95", amount="95.00",
                   title="BOG->MDE afternoon flight"),
        fake.offer(offer_id="ofr_MIA_300", amount="300.00",
                   title="BOG->MIA direct"),
        fake.offer(offer_id="ofr_HTL_120", amount="120.00", category="hotels",
                   title="Hotel Cartagena, 2 nights"),
    ]
    write("offers.json", [o.model_dump(mode="json") for o in offers])

    # ---- intents (schemas.md §9 lists these four) ------------------------
    now = int(time.time())

    def signed(intent, key=agent_key):
        body = intent.model_dump(mode="json", exclude_none=True)
        return {"intent": body, "intent_jwt": sign_detached(body, key)}

    write("intent_130_approved.json", {
        **signed(fake.intent(mandate_jti=claims.jti, offer_id="ofr_COR_130",
                             amount="130.00", now=now)),
        "expect": {"decision": "APPROVED"},
    })
    write("intent_300_escalated.json", {
        **signed(fake.intent(mandate_jti=claims.jti, offer_id="ofr_MIA_300",
                             amount="300.00", now=now)),
        "expect": {"decision": "ESCALATED",
                   "reason_code": "AMOUNT_EXCEEDS_PER_TXN"},
    })
    write("intent_wrong_category.json", {
        **signed(fake.intent(mandate_jti=claims.jti, offer_id="ofr_HTL_120",
                             amount="120.00", now=now)),
        "expect": {"decision": "REJECTED", "reason_code": "CATEGORY_FORBIDDEN"},
    })
    write("intent_wrong_key.json", {
        **signed(fake.intent(mandate_jti=claims.jti, offer_id="ofr_COR_130",
                             amount="130.00", now=now), key=wrong_key),
        "expect": {"decision": "REJECTED",
                   "reason_code": "INVALID_PROOF_OF_POSSESSION"},
        "note": "Signed by a key the mandate does not bind: the impersonation case.",
    })

    # ---- AP2 Checkout JWT ------------------------------------------------
    checkout = ap2.build_checkout_payload(
        order_id="ord_fixture_130",
        merchant_id="vuelaya",
        merchant_name="VuelaYa",
        merchant_website=MERCHANT_SITE,
        line_items=[{"id": "ofr_COR_130", "label": "BOG->COR morning flight",
                     "amount": ap2.to_minor_units("130.00"), "quantity": 1}],
        total_price="130.00",
        currency="USD",
    )
    checkout_jwt = sign_compact(checkout, merchant_key, kid=MERCHANT_KID, typ="JWT")
    write("checkout_jwt.json", {
        "payload": checkout,
        "checkout_jwt": checkout_jwt,
        "checkout_hash": ap2.checkout_hash(checkout_jwt),
        "closed_checkout_mandate": ap2.closed_checkout_mandate(checkout_jwt),
        "note": "Signed ES256 — AP2 forbids deterministic signatures here.",
    })

    print(f"\n{len(list(HERE.glob('*.json')))} fixtures in {HERE}")


if __name__ == "__main__":
    main()
