# 0022 — Dev 3 writes to Dev 2's outbox through a shared helper

Date: 2026-08-29 · Status: proposed (needs Dev 2's agreement at the daily sync)
Workstream: 3 (requires 2)
Supersedes: none (resolves a conflict inside PLAN-PARALELO §6.4)

## Context

`schemas.md` §4 assigns Dev 3 eight event types: `mandate.created`,
`mandate.activated`, `mandate.revoked`, `mandate.suspended`,
`mandate.exhausted`, `mandate.expired`, `payment_instrument.linked`,
`escalation.resolved` and `escalation.expired`.

All events land in the `outbox` table, which `schemas.md` §6 assigns to **Dev 2**.

PLAN-PARALELO §6.4 says: *"one migration per dev, only their tables. Changing
another's table is a contract change."* So the contract simultaneously requires
Dev 3 to emit these events and forbids Dev 3 from writing where they go.

This cannot be deferred: decision #10 requires the event and the business change
to commit in the **same transaction** — that atomicity is the argument for having
an outbox at all. Dev 3 cannot hand the event to Dev 2 through an API call
without breaking it.

## Chose

Split ownership between schema and access.

* **Dev 2 owns the `outbox` DDL** and its migration. Unchanged.
* **Dev 3 writes rows through one shared helper** in `trustlib`, never with
  hand-written SQL:

```python
def emit_event(session, *, type: str, aggregate_id: str, payload: dict) -> EventEnvelope:
    """Append to the outbox inside the caller's transaction (decision #10)."""
```

The helper takes the caller's session, so the insert joins the surrounding
transaction and commits with the business change or not at all. Dev 3 never
issues `INSERT INTO outbox` directly, so the column layout stays Dev 2's to
change: a migration by Dev 2 updates the helper, and every producer follows.

The event catalogue in `schemas.md` §4 stays the authority on which types exist
and who may emit them.

## Rejected

* **Dev 3 gets its own outbox table.** Two tables means two relays, two
  orderings, and a consumer that has to merge them. The `seq` ordering that the
  SSE relay and the ledger depend on would no longer be total.
* **Dev 3 POSTs events to a Dev 2 endpoint.** Breaks the atomicity that decision
  #10 exists to provide: the business change could commit while the event call
  fails, and the audit trail would be missing exactly the events that matter.
* **Dev 3 writes raw SQL against `outbox`.** Works until Dev 2 renames a column
  on day 2 and three of Dev 3's routers break at once.
* **Move `outbox` to shared ownership.** Nobody owns it, so nobody migrates it.

## Why

The rule in §6.4 exists to prevent schema collisions between people working
asynchronously, not to prevent writes. Routing every producer through one typed
function preserves what the rule protects — a single owner for the shape — while
allowing the transactional write the design requires.

It also gives one place to enforce the envelope of `schemas.md` §4, so an
event with a misspelled type or a missing `aggregate_id` fails in one function
instead of in three routers.

## Does not solve

**Dev 2 can still break Dev 3 with a migration.** The helper narrows the blast
radius to one function, but a required new column with no default still breaks
every producer until the helper is updated. Mitigation is social: outbox
migrations get announced, like contract changes.

**Nothing enforces the emitter table of §4.** Any workstream importing the
helper can emit any event type. A `producer` allowlist is possible and was not
built — it would be enforcement against ourselves, and the review catches it.

**Needs Dev 2's agreement.** Written as `proposed`; Dev 3 cannot unilaterally
decide how another workstream's table is accessed.

## Consequences for contracts

* `schemas.md` §6 — no DDL change. `outbox` stays exactly as Dev 2 wrote it.
* `schemas.md` §4 — unchanged; this records *how* the emitters write, not *what*.
* PLAN-PARALELO §6.4 — clarified: "only their tables" governs **schema
  ownership**, and cross-workstream writes go through a `trustlib` helper.
* New: `trustlib.events.emit_event`, imported by Dev 3's routers.
