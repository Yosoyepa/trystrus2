# 0019 — Four workstreams cut by capability, not by architectural layer

Date: 2026-08-29 · Status: accepted · Workstream: all
Supersedes: the A/B/C/D assignment of `docs/PLAN-PARALELO.md` v2 and the
C1/C2/C3 subdivision of v2.1 §3.1 (now dissolved).

## Context

The v2 assignment was cut by architectural layer: identity (A), decision (B),
purchase circuit (C), front (D). That cut concentrated three different
competencies in one dev — payments integration, commerce APIs, LLM
engineering — and forced the C1/C2/C3 patch to make it survivable. The team
asked for a cut that matches how they describe the work themselves.

## Chose

Four capability lanes (PLAN-PARALELO v3):

- **Dev 1 · Agentic** — `agent` service + watcher job: own graph orchestrator
  (`await_human`, `agent_runs`), signed intents, MCP client, Presidio,
  injection suite.
- **Dev 2 · Fraud, contracts, idempotency** — kernel decision core: policy
  gate, verify endpoint with atomic reservation, purchase saga +
  compensation, idempotency keys, hash-chained ledger with KMS roots, outbox
  relay.
- **Dev 3 · API backend** — kernel identity + `merchant` service: SD-JWT
  issuance, JWKS, passkey ceremony, state machine, revocation, escalations
  API, catalog + MCP tools, checkout, PayPal adapter, webhooks.
- **Dev 4 · Front & platform** — `web` (three consoles), Telegram bot,
  `infra/` (bootstrap, CI/CD, domain, secrets).

The kernel deployable hosts routers from 2 and 3 in separate folders
(CODEOWNERS, no cross-imports outside `trustlib`), plus Dev 4's bot router —
same pattern as before, different letters.

## Rejected

- One-service-per-dev: five deployables, four devs, the watcher needs a home.
- The old layer cut: its symptom was C's overload, which C1/C2/C3 only
  patched.
- Keeping C1/C2/C3: a patch for a cut that no longer exists.

## Why

Capability lanes match how failures are investigated on demo day: one dev
owns every anti-fraud invariant end to end, one owns everything the agent
does, one owns every API surface, one owns what the judges touch. Nothing
else moved: contracts, milestones M0–M5, mocks and test ownership by
component are untouched — parallelism never depended on which dev held
which component, only on the frozen contracts.

## Does not solve

Dev 3 is the heaviest lane (identity + store + rail) and a crisis in one
lane has less cushion now; mitigations are pre-agreed (catalog detaches to
Dev 1 — trivial fixtures; checkout to Dev 2 after M1; the mandate crypto
never moves). Two devs (2 and 3) also share the kernel deployable, so folder
discipline is load-bearing.

## Consequences for contracts

None structurally — `api.yaml` and `schemas.md` unchanged except owner
labels (letters → numbers).
