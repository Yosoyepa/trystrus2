# Devlog — Dev 1 · Agentic (agent service + watcher job)

Mission: an agent that discovers and proposes — structurally unable to pay
outside the gate, resilient to prompt injection. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 — guardrails, quotas and a sandbox (`limits.py`, `deploy/`)

- **Why:** asked what stops a malicious prompt polling the merchant every
  0.01 s. Checked first: the model has no tool that sets an interval, creates a
  watch or spawns a run, so the instruction is unrepresentable. Built the other
  three layers anyway.
- **Decision:** [#21](../decisions/0021-guardrails-and-containment.md).
- **Built:** `src/agent/limits.py` — persisted token buckets, windowed counters,
  single-flight locks, and guards for watch interval/count, merchant and LLM
  call rates, runs per hour, steps, wall clock, prompt size, escalations per
  approver. `deploy/sandbox.sh` (bubblewrap), hardened
  `trytrust-watch.service`, `deploy/SECURITY.md`, `cli limits`.
- **Contracts touched:** `schemas.md` §6 additionally needs `rate_buckets`,
  `locks`, `counters` (additive; folded into the same pending PR as #20).
- **Tests:** G1–G8 added, 28/28 green. G9/G10 (sandbox containment) measured by
  hand and recorded in SECURITY.md — `~/.ssh` blocked, `.env` blocked, writes to
  `$HOME` land on tmpfs, and `kernel.py` is read-only to the sandboxed process.
- **Found and fixed:** `max_seconds or QUOTA.max_run_seconds` treated an
  explicit `0` as unset, so a caller asking for no time budget got the 120 s
  default — a guardrail failing open. Same bug in `clamp_text`. Both now
  `is None`. Caught by test G6, which is the argument for writing the test
  before believing the code.
- **Open questions:**
  1. **No auth on the console** (P7) — anyone who can run the CLI can publish an
     ontology. Biggest hole now. Needs Dev 3/4 input on where auth lands.
  2. Egress allowlist (P6) is named in SECURITY.md, not built.
  3. Single-flight uses SQLite; multi-instance Cloud Run needs Postgres
     advisory locks. Dev 4.

---

## 2026-08-29 — vertical slice running end to end (`src/agent/`)

- **Why:** M0 was blocked on other lanes, so Dev 1 built against local mocks
  instead of waiting. The whole flow now runs from a clean clone with no
  external service: `reset` → `seed` → `demo`.
- **Decision:** [#20](../decisions/0020-agent-memory-ontology-and-console.md) —
  memory + ontology + configuration console. Ontology is unsigned advice,
  the mandate is signed law; neither memory nor ontology reaches the gate (S4/K1).
- **Built:**
  - `graph.py` — the own-graph orchestrator (#16): `perceive → search → propose
    → gate → await_human → receipt`, checkpointed in `agent_runs`, resumed by
    escalation resolution. Replan is keyed on `ReasonCode`: only
    `AMOUNT_MISMATCH` and `RAIL_ERROR` retry; everything else stops, because
    retrying a revoked mandate fills the log with noise.
  - `chat.py` — a person asks, interrupts, approves or redirects mid-run.
    Guidance while parked rejects the pending escalation and replans.
  - `watcher.py` — standing watches with human-set JsonLogic thresholds; a tick
    also expires escalations, which is what makes the 120 s fail-closed timeout
    real when nobody is at a terminal. cron / systemd / Cloud Scheduler in `deploy/`.
  - `registry.py` — people, agents, versioned ontologies. Publish = 3 writes,
    1 transaction. `agent_runs.agent_version` is pinned at run start.
  - `crypto/` — JCS canonicalisation, Ed25519, compact + detached JWS. Real.
  - `llm.py` — `gpt-4.1-nano` via stdlib urllib, untrusted text fenced, and a
    deterministic fallback so no network failure can take the demo down.
  - Mocks for the other lanes: `mocks/merchant.py` (catalog, 3 MCP tools,
    checkout that verifies the mandate itself), `mocks/rail.py` (vaulted token,
    idempotent capture, DELETE kill switch), `kernel.py` (the real decision table).
- **Contracts touched:** `schemas.md` §6 needs `people`, `agents`,
  `agent_versions`, `watches`, `chat_messages`, and `agent_runs.agent_version`
  — PR pending, decision record already written (docs-guard #17).
- **Tests:** `uv run python -m src.agent.tests` — 20 checks, each named after the
  property it defends ([`../PROPERTIES.md`](../PROPERTIES.md)). All green.
  Covers T1 (determinism), T3 (impersonation), T7 (resume idempotency),
  T9 (chain), T10 (JsonLogic), T11 (injection), T12 (scrubber).
- **Found and fixed:** a sticky approval issued a mini-mandate with its own
  budget, which quietly minted spending power. Reservation, settlement and
  release now walk the whole parent chain — a child can never spend what its
  parent cannot (K1/H6).
- **Open questions:**
  1. `audit_events` columns must match Dev 2's migration or we get two chains.
     Needs five minutes with Dev 2 before either migration lands.
  2. Product renamed to **TryTrust** (`trytrust.lat`). The domain does not
     resolve yet and passkeys need a real registrable domain (P2, decision #3).

---

## 2026-08-29 — workstream opened at M0 freeze
- **Why:** contracts v1.0 are frozen; this log exists so nobody re-solves what Dev 1 already solved.
- **Decision:** this workstream starts from
  [`../decisions/0016-agent-orchestration-without-langgraph.md`](../decisions/0016-agent-orchestration-without-langgraph.md):
  own ~150-line graph — LLM only in `propose`, checkpointing in `agent_runs`,
  `await_human` resumed by escalation resolution. **No agent-framework
  dependency.** Identity keys: [`../../DECISIONS.md`](../../DECISIONS.md) #9.
- **Contracts touched:** `schemas.md` §5 (escalation resume), §6 (`agent_runs`
  DDL — my migration), §10 (MCP tools I consume from Dev 3).
- **Tests I own:** T3 (impersonation), T7 (resume idempotency, with Dev 2),
  T11 (injection suite), T12 (Presidio).
- **Open questions:** none.
