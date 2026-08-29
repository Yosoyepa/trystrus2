# Devlog — Dev 3 · API backend (kernel: identity + merchant service)

Mission: every API surface exists — mandates with passkeys, escalations,
catalog + MCP tools, checkout, PayPal rail — and VuelaYa verifies the mandate
BEFORE charging. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 — fraud implementation plan (branch `dev3/fraud-transaction-research`)
- **Why:** team approved the research; next step was an executable plan.
- **Done:** [`../plans/2026-08-29-fraud-implementation.md`](../plans/2026-08-29-fraud-implementation.md)
  — 5 phases, task cards F1.1–F2.3 with owner/milestone/estimate/DoD/tests
  (T19–T25 proposed), migrations and outbox events by lane, demo acceptance
  checklist, and drafts of decisions 0020–0022 in the appendix (NOT yet
  ratified — Phase 0 is the team sign-off gate).
- **Decision:** none new (plan only). F0.1–F0.3 are the pending ratifications.
- **Contracts touched:** none yet; Apéndice B lists the exact deltas F0 will apply.
- **Tests I own:** T19 (idempotency derived key), T21 (webhook forgery), T24
  (evidence pack), T17/T14 extensions, rail parity suite.
- **Open questions:** Yuno sandbox credentials ≥48 h before demo (gate for
  F2.2); measure UV deep-link latency at M3 to validate 300 s TTL.

## 2026-08-29 — fraud-prevention research (comité, branch `dev3/fraud-transaction-research`)
- **Why:** the team asked for a fraud-defense strategy that complements (never
  replaces) 3DS, customer auth and processor validations, with official sources.
- **Done:** 4 parallel research subagents (transactional fraud, identity &
  behavior, Yuno+PayPal integrations, network standards) + committee synthesis
  resolving 10 cross-report contradictions. Full deliverable:
  [`../research/2026-08-29-fraud-transaction-research.md`](../research/2026-08-29-fraud-transaction-research.md).
- **Key verdicts:** no network liability shift on vaulted PayPal — our defense
  is *cryptographic evidence of authorization* (D-01); corroborative signals
  may only ESCALATE, verdictive ones REJECT (gold rule); P0 rules R-IDEM,
  R-PRICE, R-WEBHOOK, R-BURST, R-STEPUP, R-EVIDENCE are hackathon-feasible and
  deterministic; Yuno is integrable behind `PaymentRail` (mock faithful to the
  real contract; `stored_credentials` CIT/MIT solves our COF marking gap).
- **Decision:** none ratified here (research only). Proposed follow-ups that
  DO need decision records: YunoRail adoption (0020), level-dependent
  escalation TTL, `get_status`/`respond_dispute` on the PaymentRail protocol.
- **Contracts touched:** none.
- **Tests I own:** proposal adds coverage targets — R-WEBHOOK strengthens T14,
  R-IDEM strengthens T17, evidence pack feeds T18.
- **Open questions:** ownership split of P0 rules (proposal in the doc §12),
  Yuno sandbox credentials, granular anti-fraud consent in mandate UI
  (Colombia Ley 1581) for Dev 4.

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
