"""Where TryTrust sits among the agent-payment standards.

Kept as code rather than only prose so `trytrust protocols` can print it during
the technical defence, and so the mapping stays next to the objects it claims to
match.  Full write-up: docs/PROTOCOLS.md
"""
from __future__ import annotations

MAPPING = [
    # (AP2 concept, TryTrust object, status)
    ("IntentMandate — user pre-authorises an agent within constraints, "
     "human-not-present, carries expiry",
     "our signed mandate (purchase_mandate_v1): limits, scope, validity, "
     "conditions as JsonLogic, agent key in cnf.jwk",
     "implemented"),
    ("CartMandate — merchant-signed cart binding SKU-level price",
     "the catalog offer the merchant serves; the price is authoritative and the "
     "agent cannot restate it (S6)",
     "partial — the merchant does not yet SIGN the cart"),
    ("PaymentMandate — capture the chosen method, the amount, a hash of the "
     "cart, and the signatures the rail needs",
     "our purchase intent: detached JWS over RFC 8785 canonical JSON, naming "
     "mandate_jti + offer_id + amount, nonce, 120 s expiry",
     "implemented"),
    ("UserCartConfirmationRequired — step up to the human when the "
     "pre-authorisation does not cover the cart",
     "our escalation: gate returns ESCALATED, the run parks in await_human, "
     "and the approval re-enters the gate rather than bypassing it",
     "implemented, and stricter"),
    ("Credentials provider holds the instrument; the agent never sees it",
     "payment_method_ref is an opaque vaulted token; the rail adapter is the "
     "only thing that resolves it (O7)",
     "implemented"),
]

GAPS = [
    ("Live revocation", "AP2 defines how permission is GRANTED, not how it is "
     "taken back mid-flight. We check mandate state inside the charging "
     "transaction and DELETE the rail token, so a revoked mandate fails twice."),
    ("Dispute resolution", "AP2 produces signed artefacts but does not say what "
     "answers 'I never authorised this'. Our hash-chained bundle — mandate, "
     "signed intent, approval receipt, capture — is the answer."),
    ("Determinism of the check", "Neither AP2 nor ACP forbids a model in the "
     "enforcement path. We do (S1), which is what makes the same request give "
     "the same answer twice in front of a judge."),
]

OTHERS = [
    ("ACP — Agentic Commerce Protocol (OpenAI, Stripe, Meta; Apache 2.0)",
     "Checkout sessions against a merchant the agent does not own, plus a "
     "Delegated Payment Spec: the agent hands over a token scoped to one "
     "merchant and one cart total. Stripe's Shared Payment Token is the first "
     "implementation. Closest sibling to our checkout + vaulted token."),
    ("UCP — Google/Shopify", "Reuses AP2 mandates for the commerce layer."),
    ("Mastercard Agent Pay / Visa Intelligent Commerce",
     "Network-side agent tokens; both assume a signed-mandate object exists "
     "upstream, which is the object we issue."),
    ("FIDO Alliance — Verifiable Intent",
     "Google donated AP2 to FIDO in April 2026; FIDO took in Mastercard's "
     "Verifiable Intent alongside it in May 2026. This is why the human "
     "signature is a passkey and not a password: the ceremony is the proof a "
     "person was there, and an agent cannot perform it."),
    ("x402 / Coinbase", "Machine-native HTTP 402 settlement. Rejected here "
     "(decision #8): on-chain settlement is irreversible, and you cannot "
     "demonstrate a chargeback on something that cannot be reversed."),
]


def summary() -> str:
    lines = ["TryTrust and the agent-payment standards", "=" * 72, ""]
    lines.append("AP2 (Agent Payments Protocol) — Google + Coinbase, Sept 2025;")
    lines.append("donated to the FIDO Alliance, April 2026. Three signed mandates:")
    lines.append("Intent -> Cart -> Payment, carried as verifiable credentials.")
    lines.append("")
    lines.append("MAPPING")
    lines.append("-" * 72)
    for concept, ours, status in MAPPING:
        lines.append(f"\nAP2:       {concept}")
        lines.append(f"TryTrust:  {ours}")
        lines.append(f"status:    {status}")
    lines.append("\n\nWHAT THE STANDARDS LEAVE OPEN (our contribution)")
    lines.append("-" * 72)
    for title, detail in GAPS:
        lines.append(f"\n{title}\n  {detail}")
    lines.append("\n\nTHE REST OF THE FIELD")
    lines.append("-" * 72)
    for title, detail in OTHERS:
        lines.append(f"\n{title}\n  {detail}")
    lines.append("")
    return "\n".join(lines)
