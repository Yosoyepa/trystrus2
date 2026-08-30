# 0029 — One schema source of truth

Date: 2026-08-30 · Status: accepted
Workstream: all
Supersedes: none

## Context

The same database was described in four places that disagreed:
`src/agent/db.py:SCHEMA`, `aval/contracts/fixtures/schema.sql` (the only one
compose mounts), `src/api/db/schema.sql` (mounted by nobody), and the Alembic
migrations. The six fraud tables — `velocity_counters`, `risk_lists`,
`risk_subjects`, `baseline_metrics`, `baseline_hists`, `webhook_archive` —
existed only in the file nothing loaded, so the velocity and step-up layer was
dead in the deployed stack while `decision/repository_postgres.py` wrote to it
unconditionally. On the shared database the Python suite lost 33 tests to
column mismatches, and the agent lane had drifted out of step with its own DDL.

## Chose

`aval/contracts/fixtures/schema.sql` is the schema. It already sits under the
frozen contract surface and compose already mounts it. Everything else derives:
`src/agent/db.py` reads it from disk, Alembic applies it, `src/api/db/schema.sql`
is reduced to a pointer.

For tables both lanes share, the agent's definitions win: TEXT money, `jti`-keyed
mandates, `mandate_jti` on children. The api and merchant lanes were adapted to
that shape — `src/api/models.py`, `repository.py`, `services/escalations.py`,
`decision/repository_postgres.py`, `audit/repository_postgres.py`,
`merchant/models.py`, `merchant/catalog.py`.

`audit_events` gains one narrow exception to E1: `root_sig` may be filled in
once, on a row that is otherwise byte-identical, because the api's checkpoint
annotation needs it. The trigger compares whole rows (`to_jsonb(NEW) - 'root_sig'`)
so a column added later is protected by default.

## Rejected

**NUMERIC money.** `kernel.reserve_chain()` reserves budget by comparing the
exact previous string, so NUMERIC would break the compare-and-swap silently —
and a silent failure there is a double-spend, not an error.

**The api's shape winning.** It is the better Postgres modelling (JSONB,
TIMESTAMPTZ, real keys), but the demo runs through the agent lane and its 42
property checks. Rewriting the side that is exercised, in favour of the side
that is not, is the wrong risk on this timeline.

**A schema per lane (separate Postgres namespaces).** Cheapest to implement and
it removes the collision, but the console creates a mandate through the api and
the agent then buys against it. Splitting them breaks that flow.

**Listing protected columns in the trigger.** The first version enumerated nine
columns, which silently left `actor`, `agent_id`, `run_id` and `mandate_jti`
mutable. Comparing whole rows fails closed as the schema grows.

## Why

Four descriptions of one database is not a documentation problem, it is a
correctness problem: whichever DDL ran first won, and the other half of the
system then read its own tables wrongly. Naming one file as the truth and making
the rest derive from it is the only version of this that cannot drift back,
because there is no second place left to edit.

The agent's shape wins on evidence rather than taste — it is what the 42
property checks and the live demo exercise.

## Does not solve

The two audit chains still have two hash algorithms. They now live in one table,
partitioned by `chain_key`, but nothing verifies one against the other; a single
root over both is a cryptographic change, not a schema change (F5 stays open).

Money stays TEXT, which is the right call for the CAS and the wrong call for
anyone who later wants to `SUM()` it in SQL. `merchant/catalog.py` already needs
`CAST(... AS NUMERIC)` to sort by price.

`src/api/decision/` and `src/api/audit/repository_postgres.py` are still wired
into nothing but tests, so their alignment is verified by the suite rather than
by the running system.
