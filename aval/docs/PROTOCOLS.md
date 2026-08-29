# TryTrust and the agent-payment standards

Reviewed 29 Aug 2026. Printable during the defence: `uv run python -m src.agent.cli protocols`.

## Why this matters for the defence

The strongest answer to "did you invent this?" is "no — we implemented the
standard the industry converged on, and closed the two gaps it leaves open."
Between September 2025 and 2026 the field agreed on the shape of the object a
person signs. We build that shape.

## AP2 — Agent Payments Protocol

Google + Coinbase, announced 16 Sept 2025 with 60+ partners (Mastercard, PayPal,
Amex, Coinbase, Salesforce). v0.2 released and **donated to the FIDO Alliance in
April 2026**; in May 2026 FIDO took in both AP2 and Mastercard's *Verifiable
Intent*. Its core idea is a **mandate**: user intent turned into a
cryptographically signed, verifiable document, carried as W3C Verifiable
Credentials, chained Intent → Cart → Payment.

### The mapping

| AP2 | TryTrust | Status |
|---|---|---|
| **IntentMandate** — pre-authorises an agent within constraints (merchant allow-list, SKU limits, expiry), signed by the user on-device. The anchor for **human-not-present** flows. | Our mandate (`purchase_mandate_v1`): `limits`, `scope`, `validity`, `conditions` as JsonLogic, agent public key in `cnf.jwk`, opaque `payment_method_ref`. | **implemented** |
| **CartMandate** — merchant-signed cart binding SKU-level pricing and expiry. The anchor for **human-present** checkout. | The catalog offer. The price is the merchant's and the agent cannot restate it (S6). | **partial** — the merchant does not yet *sign* the cart |
| **PaymentMandate** — the chosen method, the total, a hash of the cart, and the signatures the settlement rail needs. | Our purchase intent: detached JWS (EdDSA) over RFC 8785 canonical JSON, naming `mandate_jti` + `offer_id` + `amount`, with a nonce and a ≤120 s expiry. | **implemented** |
| **UserCartConfirmationRequired** — defaults true; triggers a step-up when the pre-authorisation does not cover the cart. | Our escalation. The gate returns `ESCALATED`, the run parks in `await_human`, and the approval **re-enters the gate**. | **implemented, and stricter** |
| **Credentials provider** holds the instrument; the agent never sees it. | `payment_method_ref` is an opaque vaulted token; only the rail adapter resolves it. | **implemented** |

We sit squarely in AP2's **human-not-present** modality: Marta pre-authorises,
the agent acts later, and the step-up fires when the cart falls outside.

### What AP2 leaves open — and what we add

1. **Live revocation.** AP2 says how permission is *granted*, not how it is
   *taken back* while a purchase is in flight. We check mandate state inside the
   charging transaction and DELETE the rail token, so a revoked mandate fails
   twice — in our state and at the rail (M8, M9).
2. **Dispute resolution.** AP2 produces signed artefacts but does not say what
   answers *"I never authorised this."* Our hash-chained bundle — mandate →
   signed intent → approval receipt → capture — is that answer (E2, E11).
3. **Determinism of the check.** Neither AP2 nor ACP forbids a model in the
   enforcement path. We do (S1). That is what makes the same request give the
   same answer twice in front of a judge.

## ACP — Agentic Commerce Protocol

OpenAI + Stripe + Meta, Apache 2.0, co-maintained. Defines how an agent runs
checkout against a merchant it does not own: checkout session create / update /
complete, plus a **Delegated Payment Spec** where the agent hands the merchant a
token scoped to one merchant and one cart total. Stripe's Shared Payment Token
is the first implementation; the agent never sees raw card details, and
settlement, refunds and chargebacks stay with the merchant and its PSP.

Closest sibling to our checkout plus vaulted token. If VuelaYa's `checkout_charge`
grows an ACP-shaped session API, our agent speaks a published standard to any
compliant merchant — a good next step after the hackathon.

## The rest of the field

- **UCP** (Google/Shopify) — reuses AP2 mandates for the commerce layer.
- **Mastercard Agent Pay**, **Visa Intelligent Commerce** — network-side agent
  tokens. Both assume a signed-mandate object exists upstream. That object is
  what we issue.
- **FIDO Verifiable Intent** — why the human signature is a passkey and not a
  password: the ceremony *is* the proof a person was present, and an agent
  cannot perform it. That asymmetry is the design (decision #3).
- **x402 / Coinbase** — machine-native HTTP 402, gasless EIP-3009, settlement in
  seconds. Genuinely impressive and **rejected** (decision #8): on-chain
  settlement is irreversible, and reversibility is the heart of this challenge.
  You cannot demo a chargeback on something that cannot be charged back.

## Merchant reality check

Neither Rappi nor MercadoLibre exposes a buyer-side purchase API — both are
seller-side (catalog, orders received, webhooks), and Rappi's Partners API is
not self-serve: a business contact must approve onboarding. That is why VuelaYa
exists, and why the honest framing is "we implement the protocol a real merchant
would speak", not "we integrated with Rappi".

## Sources

- [AP2 repository](https://github.com/google-agentic-commerce/AP2) ·
  [technical specification](https://deepwiki.com/google-agentic-commerce/AP2/2.3-technical-specification) ·
  [AP2 explained](https://eco.com/support/en/articles/15192002-ap2-protocol-explained-google-s-agentic-commerce-standard-2026)
- [ACP repository](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol) ·
  [Delegated Payment Spec](https://developers.openai.com/commerce/specs/payment) ·
  [Stripe ACP docs](https://docs.stripe.com/agentic-commerce/acp)
- [Rappi developer portal](https://dev-portal.rappi.com/) ·
  [MercadoLibre API](https://global-selling.mercadolibre.com/devsite/api-docs)
