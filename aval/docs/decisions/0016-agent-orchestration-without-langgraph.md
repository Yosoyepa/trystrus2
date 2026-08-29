# 0016 — Agent orchestration without LangGraph

Date: 2026-08-29 · Status: accepted · Workstream: C3
Supersedes: ADR-006 as written in `docs/PLAN.md` v2.2 (LangGraph with an
OpenAI Agents SDK fallback).

## Context

The plan chose LangGraph for the agent service: first-class `interrupt()`
before payment, a Postgres checkpointer, resumable runs. Re-examined against
the real constraint — a 2.5-day build, four developers, one of them (C3)
owning the whole brain — the framework itself became the risk: a learning
curve, checkpointer configuration, dependency churn and framework debugging
concentrated in a single person under demo pressure. That is exactly the
implementation bottleneck this project cannot afford.

The team's call: a hybrid adaptation. Keep the critical ideas that fit our
solution; drop the package.

## Chose

An orchestrator we own: an explicit graph of deterministic nodes in ~150
lines of framework-free Python, with the four ideas that made LangGraph
attractive implemented directly:

1. **Graph as control flow.** Nodes
   `perceive → search → propose → gate → await_human? → pay → receipt`.
   The LLM lives ONLY inside `propose`. The agent does not choose its own
   path; the path is code we can read in one sitting and render in an audit
   UI.
2. **Checkpointing.** An `agent_runs` table (`contracts/schemas.md` §6) in
   the same Cloud SQL instance: every node transition persists
   `node + state + status`, so a run survives Cloud Run restarts and
   redeploys, and is debuggable with a SELECT.
3. **Interrupt before pay.** The `await_human` node persists
   `status='awaiting_human'` and returns; the resume is triggered by
   `escalation.resolved` and re-enters through the gate — an approval
   authorizes a retry, never a bypass (`schemas.md` §5). Idempotent by
   contract (test T7).
4. **Tools as a bounded contract.** The MCP tools contract in
   `schemas.md` §10 (three tools, outputs are data). Already ours; a
   framework would only wrap it.

## Rejected

- **LangGraph (the framework).** The three behaviors we needed are ~150
  lines written directly; everything else it brings (graph DSL, checkpointer
  abstraction, dependency surface) is cost without demo value here.
- **OpenAI Agents SDK.** `needs_approval` is convenient but couples
  orchestration to one vendor; the gate keeps us safe either way and it is
  model-agnostic.
- **CrewAI / Google ADK / Microsoft Agent Framework.** Same analysis, weaker
  fit for interrupt-and-resume semantics.
- **No orchestrator at all** (a plain loop with in-memory state): unauditable
  and unresumable after a Cloud Run cold start — fails the trial-by-fire
  requirements.

## Why

What we needed from LangGraph was behaviors, not a package. Owning the loop
means: C3 learns nothing new on day one; the checkpointer IS a table we
already run; resume semantics are the escalation contract we already froze;
and every graph transition can be emitted as an audit event (`agent.node.*`)
— the agent's trajectory becomes part of the evidence chain, which is this
project's whole point.

## Does not solve

We now own the orchestrator's bugs (graph-loop and resume races). No free
ecosystem of integrations. If the agent grows after the hackathon, wrapping
the same node functions in LangGraph is the documented path back — the nodes
are plain functions by design.

## Consequences for contracts

- `contracts/schemas.md` §6: new `agent_runs` table (C3's migration).
- `contracts/schemas.md` §5.4: `await_human` replaces `interrupt()`; the
  idempotency requirement is unchanged (T7).
- No new Python dependencies for the `agent` service.
