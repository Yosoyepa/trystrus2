# 0021 — Guardrails and containment

Date: 2026-08-29 · Status: accepted · Workstream: Dev 1
Related: #1 (no model in enforcement), #16 (own graph), #20 (memory/ontology/console)

## Context

Asked directly: what stops a malicious prompt telling the agent to query the
merchant every 0.01 seconds, and what contains an attack that gets through?

Checking rather than assuming: the model cannot express that instruction. Its
whole surface is `search_offers`, `get_offer`, `request_purchase(offer_id,
mandate_jti)`. Nothing there creates a watch, sets an interval, spawns a run or
touches the rail. Watches are created by humans through the console.

That is the real answer, and it is not enough on its own. The adjacent risks are
ours, not the model's: a human typing `--every 0`, two cron ticks overlapping, a
loop of our own making, an inference bill running overnight, and — if anything
ever does get through — a process with full access to the developer's home
directory and to the source of the gate itself.

## Chose

**Four layers, each assuming the one above failed.**

1. **Structural.** Keep the tool surface with no verb for scheduling or spending.
2. **Quota** (`src/agent/limits.py`). A persisted token bucket and windowed
   counters behind every external call, a floor under polling intervals, caps on
   watches, runs, steps, wall clock, prompt size and escalations per approver.
   Exhaustion **fails closed**: a throttled run ends `denied`, never `captured`.
   Counters live in the database so a restart is not a fresh budget.
3. **Containment** (`deploy/sandbox.sh`, hardened systemd unit). bubblewrap with
   a read-only repo, `var/` as the only writable path, no home directory.
4. **Evidence.** Every trip is an audit event; the chain is unchanged.

**Single-flight over queueing.** An overlapping tick skips. A queued backlog is
the stampede with extra steps.

## Rejected

- **Clamping a bad interval silently.** `--every 0` becoming 30 hides a
  misconfiguration or an attack. Refusing it surfaces both.
- **In-memory rate limits.** Free to restart, therefore free to defeat.
- **Queueing overlapping ticks.** Converts a slow job into a growing backlog.
- **Docker for the sandbox.** Heavier than bubblewrap, and the thing we are
  containing is one Python process, not a service.
- **Trusting the prompt to refuse.** Spotlighting raises the cost of an
  injection; it is not a control. The gate and these quotas are the controls.

## Why

The honest framing for the defence is that the first layer is the answer and the
other three are the admission that first layers fail. Rate limits do not make the
agent safe — the gate does that. Rate limits make the agent *survivable*: a
compromised one wastes tokens and fills the log with refusals instead of
exhausting a merchant, a budget, or a human approver's patience.

Layer 3 earns its place on one measured result: inside the sandbox the agent
cannot modify `src/agent/kernel.py`. Enforcement is out of the model's reach
logically *and* out of the process's reach physically.

## Does not solve

- **Network egress is still open.** The agent must reach the model and the
  merchant, so an injection can still cause outbound requests to those hosts. The
  production answer is an egress allowlist (VPC egress rules / Cloud NAT). Named,
  not built (P6).
- **No authentication on the console** (P7). Anyone who can run the CLI can
  publish an ontology or create a watch. Attributed and permanent, but not
  authorised. This is now the largest open hole.
- **Buckets are keyed per agent**, so an attacker who can mint agent ids gets
  fresh buckets. `max_watches_total` is the only global ceiling.
- **SQLite locking is single-machine.** Multi-instance Cloud Run needs Postgres
  advisory locks; same shape, different backend.
- **Found in passing:** `max_seconds or DEFAULT` treated an explicit `0` as
  unset and handed back the largest budget. Fixed with `is None`, and the same
  bug fixed in `clamp_text`. Falsy-zero in a limits path fails open, which is
  exactly the direction a guardrail must never fail.
