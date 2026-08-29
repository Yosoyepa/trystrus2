# Devlog — Dev 1 · Agentic (agent service + watcher job)

Mission: an agent that discovers and proposes — structurally unable to pay
outside the gate, resilient to prompt injection. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

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
