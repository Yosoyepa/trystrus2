# 0022 — P0 fraud-control ownership split and tests T19–T25

Date: 2026-08-29 · Status: accepted · Workstream: all
Supersedes: none (applies the capability cut of 0019 to the fraud plan)

## Context

The fraud implementation plan (dev3, Fase 1) adds seven deterministic
controls (R-IDEM, R-PRICE, R-WEBHOOK, R-BURST, R-STEPUP, R-EVIDENCE, rail
risk metadata). They had no owners, and the research's open questions listed
the split as a blocker. The capability lanes of decision 0019 already answer
"who": the gate and its rules are Dev 2; the rail, webhooks and idempotency
are Dev 3; the agent fixtures are Dev 1; what the judges touch is Dev 4.

## Chose

- **Dev 2:** R-PRICE (gate-side amount==offer check), R-BURST (velocity
  counters in the gate path), R-STEPUP reason codes and thresholds. New
  migration [2]: `risk_subjects`, `velocity_counters`, `baseline_metrics`,
  `baseline_hists`, `risk_lists`.
- **Dev 3:** R-IDEM (derived idempotency key + 45 d TTL), R-WEBHOOK
  (signature verification + cert host allow-list + pull-before-mutate +
  `webhook_archive`), R-EVIDENCE (`GET /purchases/{id}/evidence-pack`),
  rail risk metadata (FraudNet session → `PayPal-Client-Metadata-Id`), the
  Yuno mock (0020). New migration [3]: `webhook_archive`,
  `idempotency_keys` columns, `payment_instruments.fraudnet_session`.
- **Dev 1:** adversarial offer strings (`offers_adversarial.json`).
- **Dev 4:** bot diff rendering + UV deep-link UI (with 0021 TTLs).
- **Tests:** T19 (idempotency retry), T20 (price TOCTOU auto-refund), T21
  (forged webhook discarded), T22 (burst cooldown), T23 (L3+ threshold +
  UV timeout fail-closed), T24 (evidence pack complete and verifiable), T25
  (integral attack-script smoke, pre-demo).

## Rejected

- All controls to Dev 2 (the rail-touching half — webhooks, idempotency,
  evidence — is API-surface work, not gate work; it would re-create the
  overload 0019 mitigated).
- All controls to Dev 3 (the gate is Dev 2's anti-fraud invariant end to end;
  splitting it across lanes breaks the "one dev owns every anti-fraud
  invariant" property).
- Deferring ownership until M1: F1.1/F1.3 start immediately; blockers cost a
  day each.

## Why

Same principle as 0019: capability lanes match how failures are investigated
on demo day. An attack that survives the gate is Dev 2's incident; an attack
that arrives through the rail's surface is Dev 3's. The gold rule travels
with the gate: corroborative signals only ESCALATE, verdictive ones REJECT.

## Does not solve

P1 hardening (velocity matrices, EWMA baselines audited, deferred auth +
void, refund gate, reconciliation) has owners in the plan but no schedule
yet — that is post-event work by definition. T25 depends on all other tests
being green, so it doubles as the demo rehearsal gate.

## Consequences for contracts

`PLAN-PARALELO.md` §7 gains the T19–T25 rows with these owners;
`schemas.md` §6 gains the two migration blocks; §4 gains the `risk.*` /
`fraud.*` event rows; §7 gains the new reason codes. Realization: plan
Fase 1 task cards F1.1–F1.8.
