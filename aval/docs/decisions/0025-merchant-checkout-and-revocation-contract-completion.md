# 0025 — Make the merchant's cryptographic checks and revoke ceremony expressible

Date: 2026-08-30 · Status: accepted
Workstream: 3 (affects 1, 2 and 4)

## Context

The frozen v1.0 transport contract described checks that it could not carry.
`PurchaseIntent` is a detached JWS, but `ChargeRequest` contained only its
signature — not the canonical payload required to reattach and verify it.
Likewise the checkout path was required to compare `checkout_hash`, but no
Checkout JWT reached the merchant. The catalogue plan also named individual
offer and hot-price routes that had not made it into OpenAPI.

There was a second, independent blocker in revocation: activation and
revocation correctly use the same canonical mandate hash as their WebAuthn
challenge, while `webauthn_challenges` keyed rows on that hash alone. After an
activation, a real revoke ceremony could not be stored.

Leaving any of these implicit would make the demo either unverifiable or force
an unsafe convenience path that skips proof-of-possession.

## Chose

Advance the merchant contract to **v1.1** and make the missing evidence
explicit:

* `POST /checkout/quote` signs and persists an ES256 Checkout JWT before the
  agent signs its hash. `POST /checkout/charge` carries the canonical intent,
  detached JWS, mandate id, and Checkout JWT. VuelaYa now runs the exact
  seven-step sequence: issuer SD-JWT → agent proof → current offer price →
  stored Checkout JWT/hash → kernel verify → rail → receipt.
* Add `GET /catalog/offers/{id}` and `POST /admin/offers/{id}/price`, plus
  `origin`, `destination`, and `date` on offers so the documented filters are
  real data filters rather than title-string heuristics. `merchant_orders`
  persists the signed checkout bytes and receipt. It locks a cart while it is
  charged and permits one `purchase_id`, so a retry returns the stored receipt
  rather than reaching the rail again.
* Replace the stale PayPal webhook surface with the additive
  `/webhooks/yuno` endpoint. The simulated orchestrator publishes a JWKS and
  signs canonical `EventEnvelope`s with detached Ed25519 JWS; invalid
  signatures return 401.
* Add a fresh `POST /mandates/{id}/revoke/options` ceremony and key WebAuthn
  challenges by `(challenge, purpose)`. The same signed mandate hash remains
  the challenge; purpose makes activation and revocation independently
  single-use. Revocation persists opaque instruments at activation, commits
  mandate state first, then deletes every rail token.

`PurchaseIntent.checkout_hash` was previously optional for cross-team
compatibility. The merchant's charge route now fails closed when it is absent;
the agent and gate must carry it for the v1.1 happy path.

## Rejected

* **Parsing an amount or intent from an opaque detached JWS.** A detached JWS
  has no payload segment. Reconstructing one from request fields would make a
  signature check theatrical.
* **Generating a Checkout JWT only after receiving the intent.** The agent
  could not have signed its hash, so the cart would not be bound.
* **Treating price filters as title searches.** It makes a date filter silently
  lie to the watcher and gives adversarial descriptions an accidental role in
  matching.
* **Reusing the consumed activation challenge for revoke.** That weakens the
  single-use record and fails outright for counter-less authenticators.
* **Accepting unsigned webhooks for the simulated rail.** A local simulator is
  still a separate security principal; its events need an origin proof.

## Why

The merchant is the last independent verifier before money moves. Its checks
must be reproducible from the bytes it receives and from its own catalogue,
not from an assertion that another service already checked them. Persisting
the checkout freezes exactly what VuelaYa committed to, while the current
catalogue comparison deliberately makes a price change invalidate an old
quote instead of silently repricing it.

The composite challenge key keeps the strongest part of the passkey design:
the browser signs the exact mandate hash for both granting and removing
authority. It adds neither random session state nor a weaker revocation path.

## Does not solve

MCP's frozen `request_purchase(offer_id, mandate_jti)` shape still has no
place for Dev 1's signed intent. The merchant submits only those two safe
references and never signs an intent or charges; Dev 2's `/purchases` handoff
needs the agreed agent-context mechanism before that MCP tool can complete a
real purchase. This record deliberately does not smuggle an `intent_jwt` into
the visible tool arguments.

The simulated rail remains a simulation: a signed webhook proves that our
orchestrator emitted an event, not that an external payment network did.
