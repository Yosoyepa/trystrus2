# Devlog — Dev 3 · API backend (kernel: identity + merchant service)

Mission: every API surface exists — mandates with passkeys, escalations,
catalog + MCP tools, checkout, PayPal rail — and VuelaYa verifies the mandate
BEFORE charging. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 — C0 bootstrap service skeleton
- **Why:** opened the implementation lane with a runnable Python 3.13/FastAPI
  service and a stable persistence/configuration boundary for the fraud P0.
- **Done:** added uv dependencies and ruff/pytest configuration, typed env
  settings, the thin root entrypoint, FastAPI factory with `/healthz`, the
  renamed `src/api/repository.py` in-memory boundary, and the v1.1 PostgreSQL
  seed DDL including idempotency, risk, rail metadata and webhook tables.
- **Tests:** `src/api/tests/test_smoke.py`; `uv run pytest` and
  `uv run ruff check .` pass at this phase.
- **Contracts touched:** none; frozen `aval/contracts/` remains unchanged.
- **Open questions:** none for C0; persistence adapters and policy ports land
  in the following phases.

---

## 2026-08-29 — branch updated with main (src/ landed); coder brief issued
- **Why:** main gained `src/` (empty `src/api` scaffold + placeholder
  `src/agent/text.txt`, uv + Dockerfile at root, docs-guard now covers
  `src/`); our branch needed it before implementation starts.
- **Done:** merged `origin/main` (eff60c7..22ba154) into
  `dev3/fraud-transaction-research` — clean, no conflicts. Issued the coder
  brief: [`../plans/2026-08-29-fraud-coder-brief.md`](../plans/2026-08-29-fraud-coder-brief.md)
  — full prompt implementing Fase 1 + F2.1/F2.3 in `src/` ONLY (code never in
  `aval/`), 9 closed commits (C0 bootstrap → C8 fixtures + T25 smoke),
  clean-architecture layout for `src/api` (router/services/repository/ports/
  domain/rail/webhooks/evidence), invariants (no enforcement ML, gold rule,
  fail-closed, frozen contracts), env table, test strategy, DoD and report
  format. Dev 3 verifies the commit range + suite afterwards.
- **Decision:** none new (brief executes 0020–0022).
- **Contracts touched:** none.
- **Open questions:** none — verification pending coder's completion report.

## 2026-08-29 — Phase 0 ratified: decisions 0020–0022 executed (contracts v1.1)
- **Why:** team answered the four blocking questions; the plan's Fase 0 gate is green.
- **Done:** records `0020-yunorail-adapter` (accepted **mock-only variant**: no Yuno
  sandbox credentials, so the real adapter moves to Fase 4 and the demo runs
  `AVAL_RAIL=paypal|yuno_mock`), `0021-escalation-ttl-by-level` (L3 120 s · L3+
  300 s UV, both fail-closed), `0022-p0-ownership-split` (T19–T25 by lane).
  `DECISIONS.md` → 22 entries; `schemas.md` v1.1 (PaymentRail +`get_status`/
  `respond_dispute`, `open_dispute` deprecated, HMAC(jti) idempotency note,
  risk/webhook DDL, step-up reason codes, mock-yuno); `api.yaml`
  (+`/purchases/{id}/evidence-pack` + `Escalation.level` + TTL docs);
  PLAN-PARALELO §7 rows for T19–T25; plan doc marked ratified.
- **Decision:** 0020 (mock-only), 0021, 0022 — full records in `docs/decisions/`.
- **Contracts touched:** `aval/contracts/schemas.md`, `aval/contracts/api.yaml`
  (additive v1.1, records in the same commit per protocol #17).
- **Tests I own:** unchanged set plus T19/T21/T24 confirmed mine by 0022.
- **Open questions:** measure UV deep-link latency at M3 (validates the 300 s
  TTL); trustlib `EvidencePack` model lands with F1.6.

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
