# Devlog — Dev 3 · API backend (kernel: identity + merchant service)

Mission: every API surface exists — mandates with passkeys, escalations,
catalog + MCP tools, checkout, PayPal rail — and VuelaYa verifies the mandate
BEFORE charging. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 21:10 — the identity HTTP surface; mandates live end to end
- **Why:** the services existed but nothing was reachable. Dev 4 cannot build a
  console against a Python class, and M1 needs a real SD-JWT over the wire.
- **Decision:** none new. One **additive** interface note below.
- **Contracts touched:** `schemas.md` §3 gains **`AsyncPaymentRail`** — a
  sibling of `PaymentRail`, same names/semantics/returns, awaited. The frozen
  `PaymentRail` is untouched. Reason: §3 was written when the rail was assumed
  in-process; it is now an HTTP call (0024) and the kernel is async, so a
  blocking call would stall the event loop for every other request — including
  the verify a live purchase is waiting on.
- **Tests:** 192 green. 15 repository tests against **real Postgres** (SQLite
  cannot express the guarded UPDATE, and that UPDATE *is* the state machine),
  including concurrent revoke-vs-revoke and activate-vs-revoke. 9 HTTP tests
  drive the **real passkey ceremony** with a software authenticator.
- **Open questions:**
  - **Dev 4 — the endpoints you can build against now:**
    `GET /.well-known/jwks.json`, `POST /passkeys/register/{begin,complete}`,
    `POST /mandates`, `GET /mandates?user_id=`, `GET /mandates/{id}`,
    `POST /mandates/{id}/passkey/assert`, `POST /mandates/{id}/revoke`.
    Run it: `uv run uvicorn api.main:app --app-dir src --port 8001`.
  - **Dev 4 — `POST /mandates` returns 412 if the user has no passkey.** That
    is deliberate: an agent cannot complete a WebAuthn ceremony, so consent has
    nowhere to come from. Register the passkey first in your flow.
  - **Dev 2 — revocation commits before the rail call, on purpose.** Order is
    gesture → (state + event, one transaction) → commit → delete rail token.
    The moment that commit lands, your verify starts refusing. A rail failure
    is logged and never rolls the revocation back: the mandate is dead either
    way. If you see an orphaned token in `payment_instruments`, that is the
    reconciliation path, not a bug in revocation.
  - **Dev 2 — I never write `reserved_amount`, `spent_total` or
    `txn_count_period`.** They are on my table but they are yours (§6
    convention); the ORM model marks them. Say if you want them moved.

---

## 2026-08-29 20:05 — identity core: keys, state machine, passkey ceremony, registry
- **Why:** G1 is the gate I lead and the one judges attack live. Everything else
  in the lane (checkout, revocation, escalations) reads this state.
- **Decision:** none new — implements [`0021`](../decisions/0021-webauthn-credentials-table.md)
  and the AP2 shape from [`0023`](../decisions/0023-ap2-current-mandate-model.md).
- **Contracts touched:** none. Implements `MandateRegistry` from `schemas.md` §3
  exactly as frozen.
- **Tests:** **T8 green** (82 cases — every one of the 36 (from,to) pairs against
  a spec table written independently of the implementation, so the two can be
  compared rather than assumed). Registry 12, passkey 11. **168 total.**
- **Open questions:**
  - **Dev 4 — `rp_id` is config, and it matters to you.** Passkeys are refused on
    `*.run.app` (Public Suffix List, ADR-018). Dev default is `localhost`;
    staging needs the real domain in `AVAL_RP_ID` *and* `AVAL_RP_ORIGIN`, or the
    ceremony fails with an error that looks like a code bug and is not.
  - **Dev 2 — `build_claims` and `sign` are deliberately separate.** The passkey
    challenge is the canonical hash of the claims, so they must be final before
    the gesture; but signing before the human agrees would produce a valid
    mandate nobody authorized. The ceremony goes between the two calls.
  - **Everyone — the mandate hash is order-independent.** A mandate rebuilt from
    the DB hashes the same as the one Marta signed, because both go through
    `canonical_json`. If you ever build claims by hand, use `MandateClaims`,
    not a dict.
  - **Sticky mini-mandates never outlive their parent** (`derive` clamps `exp`).
    Approving an escalation narrows authority; it must not extend delegation in
    time.

---

## 2026-08-29 19:20 — fixtures, local Postgres, and a fixture-driven verify mock
- **Why:** M0's exit criterion is a test that actually consumes the canonical
  fixtures and approves/rejects them correctly. Also: my checkout cannot be
  built before M1 without something to call in place of Dev 2's verify.
- **Decision:** none new. Implements the DDL of `schemas.md` §6 plus the tables
  from [`0021`](../decisions/0021-webauthn-credentials-table.md) and
  [`0024`](../decisions/0024-yuno-style-ap2-orchestrator-instead-of-paypal.md).
- **Contracts touched:** `contracts/fixtures/` and `contracts/mocks/` created
  (both were empty). No change to `api.yaml` or `schemas.md`.
- **Tests:** 63 green. Fixture consumption (13) proves Dev 1 and Dev 2 can sign
  and verify against these files without running my service. `mock_verify` (20)
  includes a test that fails if anyone "simplifies" the mock into approve-all.
- **Open questions:**
  - **Dev 2 — evaluation order is yours to decide, and I had to pick one.**
    Several rules fail at once and only one reason reaches the buyer. I used:
    state → validity → scope → price match → budget/count → **per-txn
    (ESCALATE)** → conditions. Rationale: state beats money (a revoked mandate
    refuses $1); hard limits before the escalatable one, so we never ask Marta
    to approve something that fails anyway after the re-run.
  - **Dev 2 — the canonical fixture states the same threshold twice, and it
    breaks the HIL demo.** `schemas.md` §9 pins `max_per_txn: 150` *and*
    `conditions: {"<": [offer.price, 150]}`. Every over-limit purchase violates
    both, so escalation on that mandate is a **dead end**: Marta approves, the
    gate re-runs per §5, the condition still refuses. My ordering keeps
    PLAN.md §7's "$300 > $150 with buttons" scene alive, but the real fix is in
    the fixture — the ceiling and the buy-trigger should be different numbers
    (spend up to $200 if asked; buy unprompted only under $150). §9 froze it, so
    I did not change it alone. `test_canonical_fixture_states_the_same_threshold_twice`
    documents it and will keep failing loudly if the semantics drift.
  - **Everyone — no Docker on this machine.** `docker-compose.yml` exists, but
    `scripts/db-bootstrap.sh` works against either a local Postgres or the
    compose one. Don't assume Docker in CI scripts.
  - **Dev 1:** `contracts/fixtures/offers_adversarial.json` is still yours to
    fill — I built `offers.json` and will mount whatever strings you add.

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
