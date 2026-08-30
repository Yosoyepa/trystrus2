# 0024 — The rail becomes a Yuno-style AP2 orchestrator we simulate

Date: 2026-08-29 · Status: accepted
Workstream: 3 (affects all)
Supersedes: decision #8 (Payments: mock → PayPal sandbox)

## Context

Decision #8 chose the PayPal sandbox as the payment rail, explicitly rejecting
"a pure internal mock". We then went looking for how to speak AP2 to PayPal —
`ADR-001` commits us to AP2 alignment — and found there is nothing to speak to.

Verified against the specification and vendor documentation, Aug 2026:

* **No AP2 endpoint is publicly reachable at any provider.** The protocol site
  publishes no partner endpoints or sandboxes; its "getting started" is
  *"download and run our code samples"*. PayPal's own developer post lists
  "Payment Mandate pilots, challenge orchestration, dispute evidence
  integration, delivering APIs and adapters for mandate creation and storage"
  as things it *intends* to deliver, with no endpoints. Adyen, Worldpay,
  Mastercard, Amex and Revolut are launch partners with the same status.
* The only externally callable AP2 extension is **x402/Coinbase** (Base
  Sepolia), which is irreversible and therefore cannot carry the dispute flow
  (`ADR-014` already established this).
* So PayPal Vault would have been reached over its ordinary REST API, with AP2
  living entirely on our side of the wire anyway.

Meanwhile the sponsor of this challenge is **Yuno, a payment orchestrator** —
precisely the pair of roles AP2 calls Credential Provider and Merchant Payment
Processor.

## Chose

`src/yuno_sim/`: a Yuno-style payment orchestrator, **simulated**, that speaks
AP2. A separate deployable with a real network boundary, not an in-process
fake — an in-process mock proves nothing about the integration.

It exposes an orchestrator-shaped REST surface that maps one-to-one onto the
`PaymentRail` Protocol of `schemas.md` §3, **whose signatures do not change**:
enrollment → vaulted token → capture → dispute → DELETE.

The load-bearing part is `ap2_verifier.py`. Before settling, the orchestrator
independently verifies the mandate SD-JWT against the kernel's JWKS, the
`checkout_hash` binding against the merchant's signed Checkout JWT, and
possession of the `cnf` key. If the mandate is revoked or the binding does not
hold, there is no settlement.

Everything it emits is labelled `simulated: true`, in code, logs and UI. It is
presented as "what a Yuno AP2 surface could look like", never as Yuno.

## Rejected

* **PayPal sandbox (decision #8, the incumbent).** Its AP2 surface does not
  exist; we would have demoed ordinary Vault REST while claiming AP2 alignment.
  It also carried assumption S13 — that sandbox disputes work over
  wallet-vaulted transactions — unvalidated, on the bonus flow.
* **x402 as the primary rail.** No reversal on-chain, so no dispute (`ADR-014`).
  Retained as a documented second `PaymentRail` for day 3.
* **An in-process fake.** Cheaper, but it tests our own imagination rather than
  an integration; no serialization, no network failure, no idempotency across
  processes.
* **Waiting for a real AP2 endpoint.** Not available on any timeline we have.

## Why

Three reasons specific to this project.

**We can be more AP2-conformant, not less.** The parts of AP2 that matter here
— accepting a Payment Mandate, verifying it before moving money, binding it to
a signed checkout — are exactly the parts no provider has shipped. Controlling
both sides is what lets us implement them at all.

**The demo stops depending on someone else's uptime.** PLAN §7 already listed
"PayPal sandbox down" as a risk with a mock as its mitigation. This promotes
the mitigation to the design and deletes assumptions S1 and S13.

**It speaks to the room.** Judges who run a payment orchestrator can evaluate a
proposed AP2 surface for a payment orchestrator. `payment_method_ref` was
always meant to be rail-agnostic (ADR-007's Yuno note); this is that thesis
executed rather than asserted.

## Does not solve

**No real money moves.** This is the honest cost of reversing decision #8, and
that decision's reasoning was not wrong — a real rail does two things a
simulation cannot: it produces a dispute object we did not author, and it fails
a payment after revocation for reasons outside our control.

**Our tests cannot discover what we failed to imagine.** Partial captures,
settlement latency, provider-side rate limits, currency edge cases and network
partitions exist in the simulation only to the extent we modelled them. A real
integration surprises you; this will not.

**Conformance is self-asserted.** We verify our AP2 objects against the
specification and the reference implementation's shapes, but no third party
validates us. `x402` on day 3 is the only path to being checked by a rail we
did not write, and it is a stretch goal.

## Consequences for contracts

* `schemas.md` §3 `PaymentRail` — **unchanged**. That is the point.
* `schemas.md` §4 — rail event names become `payment.*` / `dispute.*` from the
  orchestrator instead of PayPal webhook names. Additive.
* `api.yaml` — `/webhooks/paypal` becomes `/webhooks/yuno`. Breaking, and
  allowed only before M2 (PLAN-PARALELO §6.2); no consumer has built against it.
* New tables owned by Dev 3, in the orchestrator's own schema.
* `PLAN.md` assumptions **S1** and **S13** are void; risk "PayPal sandbox down"
  is void. `ADR-007` and `ADR-014` are amended by this record.
* T17 and T18 now run with no network and no credentials — they became
  deterministic, which is why they can run in CI.
