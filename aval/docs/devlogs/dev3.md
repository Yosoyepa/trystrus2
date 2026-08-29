# Devlog — Dev 3 · API backend (kernel: identity + merchant service)

Mission: every API surface exists — mandates with passkeys, escalations,
catalog + MCP tools, checkout, PayPal rail — and VuelaYa verifies the mandate
BEFORE charging. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 18:40 — trustlib v0.1 + AP2 conformance layer; T2 green
- **Why:** nothing existed yet — no trustlib, no fixtures, no DB. Dev 3 issues
  the mandates, so the shared crypto library is mine to write first. Everything
  else in this lane sits on top of it.
- **Decision:** four records written —
  [`0020`](../decisions/0020-python-code-lives-under-src.md) (code under `src/`
  + docs-guard follows it), [`0021`](../decisions/0021-webauthn-credentials-table.md)
  (the DDL had no table for passkeys),
  [`0022`](../decisions/0022-cross-workstream-outbox-writes.md) (**proposed** —
  needs Dev 2), [`0023`](../decisions/0023-ap2-current-mandate-model.md) (AP2
  realigned), [`0024`](../decisions/0024-yuno-style-ap2-orchestrator-instead-of-paypal.md)
  (**supersedes decision #8** — PayPal out, simulated Yuno-style AP2
  orchestrator in).
- **Contracts touched:** `schemas.md` §1 (`vct` + `constraints[]`, additive),
  §2 (`checkout_hash`, **optional**), §6 (two passkey tables, additive).
  `PaymentRail` in §3 **unchanged** — that was the design goal.
- **Tests:** **T2 green** (15 cases: valid, 1-byte mutation, foreign issuer,
  expired, not-yet-valid, unknown kid, rotation grace, forged disclosure, plus
  five key-binding cases). AP2 conformance green (14 cases). 29 total.
- **Open questions:**
  - **Dev 2:** 0022 needs your yes. You keep the `outbox` DDL; I append through
    `trustlib.events.emit_event(session, ...)` so the event commits in the same
    transaction as the business change (decision #10 requires that — an API call
    would break it). Shout if you'd rather own the helper.
  - **Dev 1:** your `PurchaseIntent` did **not** change. `checkout_hash` is
    optional and I do not require it. Full AP2 binding would have you sign over
    it — that's a v1.1 proposal, not something I'm forcing mid-freeze.
  - **Everyone:** there is **no public AP2 endpoint anywhere** — PayPal, Adyen,
    Worldpay, Mastercard and Amex have all announced and shipped nothing. Don't
    spend hours looking for one; I did. Only x402/Coinbase is callable, and it
    can't do disputes.
  - **Careful with Ed25519:** AP2 forbids it for the Checkout JWT (deterministic
    → rainbow-table on a hash-bound cart). That artefact is ES256; everything
    else stays Ed25519. `tests/test_ap2.py` asserts the difference empirically.

---

## 2026-08-29 — workstream opened at M0 freeze
- **Why:** contracts v1.0 are frozen; this log exists so nobody re-solves what Dev 3 already solved.
- **Decision:** none yet. Starting points:
  [`../../DECISIONS.md`](../../DECISIONS.md) #2 (mandate), #3 (passkey),
  #4 (revocation + token DELETE), #8 (PayPal sandbox), #15 (keys).
  Interfaces: `schemas.md` §3 (`MandateRegistry`, `PaymentRail`).
- **Contracts touched:** none.
- **Tests I own:** T2 (SD-JWT), T8 (state machine), T14 (webhooks), T17
  (rail); T18 (dispute) jointly with Dev 2. This is the heaviest lane
  (decision #19 names the mitigations: catalog → Dev 1, checkout → Dev 2
  after M1).
- **Open questions:** none.
