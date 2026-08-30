# 0023 — AP2 realigned to the current mandate model (and one forbidden curve)

Date: 2026-08-29 · Status: accepted
Workstream: 3 (affects 1 and 2)
Supersedes: amends ADR-001 and decision #2

## Context

`ADR-001` and decision #2 describe AP2 as chaining **Intent → Cart → Payment**
mandates. That was the September 2025 framing. The specification has since
changed shape, and we were about to build against a model that no longer exists.

Current AP2 (verified Aug 2026 against `ap2-protocol.org/ap2/specification/`
and the Apache-2.0 reference implementation):

* **Two** mandate families — **Checkout** and **Payment** — each in an **open**
  variant (reusable, carrying `constraints[]` and a `cnf` key) and a **closed**
  variant (one transaction), encoded as **SD-JWT** with a `vct` type claim.
* The merchant signs a **Checkout JWT**; the payment binds to it by
  `checkout_hash`, so the cart cannot be swapped after approval.
* Five roles, including a non-agentic **Trusted Surface** that obtains consent.

Reading it against our build, most of it was already true. One object was
missing entirely, and one of our crypto choices is forbidden.

## Chose

**1. Name what we already have.** Our mandate SD-JWT *is* an AP2 Open Payment
Mandate. It now carries `vct: "mandate.payment.open.1"` and a `constraints[]`
projection of its own limits (`payment.amount_range`, `payment.budget`,
`payment.allowed_payees`, `payment.agent_recurrence`,
`payment.execution_date`). The projection is **derived** from the native fields
of `schemas.md` §1, never a second source of truth — the gate keeps reading
`limits`/`scope`/`validity`, so Dev 1 and Dev 2 are untouched.

**2. Build the missing object.** VuelaYa now signs a **Checkout JWT**
(`order_id`, `merchant`, `line_items`, `total_price`, `currency`, policies) and
checkout verifies `checkout_hash` before charging. Previously the merchant
signed nothing at all.

**3. Use ES256 there, and only there.** The specification is explicit:

> "To prevent rainbow table attacks, the Checkout JWT MUST be signed using a
> digital signature scheme (e.g., ECDSA) and not a deterministic signature
> (e.g., Ed25519)."

We are Ed25519 everywhere (decisions #9, #15). The merchant gets a **separate
P-256 key** for this one artefact, published in the JWKS alongside the issuer's.
`tests/test_ap2.py` asserts the property empirically: ES256 produces a different
signature each time, Ed25519 does not.

**4. Leave the agent's intent alone.** Full AP2 binding would have the agent
sign over `checkout_hash`. That means changing `schemas.md` §2 — frozen contract
for Dev 1 (who signs) and Dev 2 (who verifies). `PurchaseIntent.checkout_hash`
exists as an **optional** field; the merchant-side binding is complete and
correct without it. Making it required is a v1.1 proposal for the daily sync.

**5. Do not depend on an AP2 SDK.** Google's Python SDK is unpublished
(`uv pip install git+...`) and still models the old generation. The `ap2`
package on PyPI (0.1.1) is a **third-party mirror** (`whillhill/ap2`), not
official. Neither belongs in an enforcement path for a graded deliverable. We
implement the shapes in `trustlib/ap2.py` and cite the specification.

## Rejected

* **Full conformance now** (four mandates, all eight constraint types, delegate
  chain, `sd_hash` linkage). It rewrites the crypto contract on day 1 and drags
  two other workstreams; the marginal defence over the above is small.
* **Documentation-only alignment.** Cheapest, but "AP2-inspired" is a weaker
  claim than "AP2-conformant" in front of judges who know the protocol.
* **Requiring `checkout_hash` in the intent immediately.** Correct end-state,
  wrong moment — it breaks a frozen contract before the owners have agreed.
* **Ed25519 for the Checkout JWT anyway.** The spec names the attack. Ignoring
  it while claiming conformance is worse than not claiming conformance.

## Why

The projection costs almost nothing and converts a claim into something a
verifier can check. The Checkout JWT is ~2 hours and closes a real hole: today
nothing stops the cart from being restated at charge time, because the merchant
never committed to it cryptographically. Comparing `intent.amount` to
`offer.amount` field by field can be fooled by whatever the comparison forgot to
look at; a hash over the merchant's own signed bytes cannot.

## Does not solve

**We are conformant on the objects, not the choreography.** AP2's full flow has
the Shopping Agent presenting mandates across all five roles; ours moves through
our own gate and verify. The mandates are AP2; the transport around them is
Aval's.

**No third party validates our conformance.** We check against the spec text and
the reference shapes. Nobody else does — the only rail that could is x402, and
that is a day-3 stretch.

**The binding is one-sided until the intent carries it.** The merchant proves
what it offered; the agent does not yet countersign what it accepted.

## Consequences for contracts

* `schemas.md` §1 — `vct` and `constraints[]` added to `MandateClaims`.
  **Additive**, both optional; existing consumers unaffected.
* `schemas.md` §2 — `PurchaseIntent.checkout_hash` added, **optional**. Becoming
  required is a separate v1.1 decision.
* `api.yaml` — the merchant JWKS now publishes an `ES256` key beside the
  issuer's `EdDSA` key. Additive.
* New: `trustlib/ap2.py` and `tests/test_ap2.py`.
* Amount encoding is converted at the AP2 boundary (AP2 uses integer minor
  units; our contract keeps 2-decimal strings). The contract does not change.
* `ADR-001` and decision #2 are amended by this record.
