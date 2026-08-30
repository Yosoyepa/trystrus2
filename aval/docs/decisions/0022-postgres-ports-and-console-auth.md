# 0022 — Postgres, ports, partitioned chains, and console auth

Date: 2026-08-30 · Status: accepted · Workstream: Dev 1
Related: #10 (outbox), #11 (Cloud Run + Cloud SQL), #16 (own graph),
#20 (memory/ontology/console), #21 (guardrails)

## Context

The agent lane worked and was built for one machine: SQLite, a single global
hash chain, one hard-wired merchant, one rail, and a console anyone who could
run the CLI could edit. Four things had to change before it could be called
scalable, extensible, or safe to expose, and none of them needed another lane.

## Chose

**Postgres everywhere.** `compose.yaml` locally, Cloud SQL in production, one
Alembic migration for both. The port fits behind `db.Conn`, a wrapper that
translates `?` to `%s` and returns dict rows, which left ~145 existing call
sites untouched — the migration could not quietly change behaviour, and the
proof is that all 28 properties passed unchanged the first time it ran.

**One chain per mandate.** A single chain meant writing entry N required
reading entry N-1, so every event in the system queued on one row. Each mandate
now has its own chain; a signed checkpoint over every chain head restores one
global proof.

**Ports, not conditionals.** Merchants, rails, models and channels are
protocols with registries. The one that earns its place is `ToolRegistry`: a
tool may declare `read` or `submit` and the constructor refuses anything else,
so S2 becomes one assertion instead of a code-reading exercise.

**Tokens on the console.** Mutations need a bearer token, hashed at rest.
The role grants a capability; attachment to the agent grants the instance.

**An egress allowlist in code.** Every outbound call goes through the model
client or the MCP transport, and both check the host first.

## Rejected

- **A dual SQLite/Postgres backend.** Two backends means one of them is
  untested, and the whole point was that dev and prod stop differing.
- **NUMERIC for money.** Amounts are fixed 2-decimal strings wherever they are
  signed (M7) and the reservation is a compare-and-swap on the exact previous
  value (M2). String equality is that semantics; NUMERIC would make it depend
  on how the server normalises `0.00` against `0`.
- **A global chain with a dedicated sequencer.** A simpler story for a judge,
  capped at one writer, and a single point of failure.
- **Converting currencies inside the gate.** A silent conversion in an
  enforcement path is a way to spend more than a person agreed to. A COP
  merchant needs a COP mandate, and a mismatch is refused.
- **Flattening merchant vocabularies into our three generic tools.** Seat maps
  and cart review are real commerce; the adapter translates rather than
  imposes.
- **Auth in the registry layer.** Authentication belongs at the edge where
  untrusted input arrives — the CLI and `service.py` — not smeared through
  every function that writes a row.

## Why

Each of these was already written down as a limitation, and a limitation
nobody removes eventually reads as a limitation nobody could remove.

The console one is worth stating plainly: it recorded who *claimed* to make a
change. An audit trail of unverified claims is weaker than it looks, because
the attribution is only as good as the honesty of whoever typed the name. This
does not touch the buyer's authority — that still comes from a passkey over the
mandate — and no token here can widen a spending limit (K1). The worst an admin
can do is make the agent propose stupid things.

## Does not solve

- **Bearer tokens are bearer tokens.** A leaked one is usable until revoked,
  and there is no rotation, expiry or revocation list yet.
- **The egress allowlist is in-process.** A compromised process can still open
  a socket directly; VPC egress rules remain the production answer.
- **Chain partitioning changes what a checkpoint means.** A root signed before
  migration 0002 describes the old single chain and will not match. That is
  correct — the evidence structure changed — but it is a discontinuity someone
  will have to explain.
- **`_destination_from()` parses a title** to recover a route from remote
  merchants. It is a hint for the prompt and never an input to a decision, but
  it is a heuristic sitting in the memory path.
- **SQLite advisory-lock parity.** Single-flight now uses Postgres advisory
  locks, which are correct across instances; nothing verifies behaviour when
  the database itself fails over mid-lock.
