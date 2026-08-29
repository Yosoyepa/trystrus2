# 0017 — Documentation protocol: devlogs + decision records, enforced by CI

Date: 2026-08-29 · Status: accepted · Workstream: all
Implements: the repo structure the team asked for, so that any agent (AI or
human) joining mid-flight can contextualize itself and nobody duplicates work.

## Context

Four developers plus AI coding agents work asynchronously against frozen
contracts. The failure mode in this shape of team is not bad code — it is
duplication and lost context: two streams re-solving the same problem because
neither knew the other had, and decisions made in chat that nobody can find
two days later. Documentation that depends on discipline dies on day 2 of a
hackathon.

## Chose

A repository structure that makes documentation unavoidable rather than
encouraged:

- **One append-only devlog per workstream** —
  [`../devlogs/{A,B,C1,C2,C3,D}.md`](../devlogs/). Every PR that changes code
  appends an entry (what, why, decision link, contracts touched, tests, open
  questions). Newest first; old entries are never edited.
- **Full decision records** — `NNNN-slug.md` files in this directory, from
  [`TEMPLATE.md`](TEMPLATE.md), written when the call is made.
  [`../../DECISIONS.md`](../../DECISIONS.md) remains the short index and the
  graded deliverable.
- **A CI guard** — [`scripts/docs-guard.sh`](../../../scripts/docs-guard.sh)
  run by [`.github/workflows/docs-guard.yml`](../../../.github/workflows/docs-guard.yml)
  and usable locally: rejects a PR that changes code without touching a
  devlog, or changes anything under `contracts/` without a decision record.

## Rejected

- Trusting everyone to write docs (dies under time pressure).
- A wiki or external docs site (drifts from the code immediately).
- End-of-project documentation (by then it is fiction, not record).
- A CONVENTIONS.md with no enforcement (read once, ignored forever).

## Why

The guard makes skipping documentation impossible to merge, which is the only
form of "mandatory" that survives a hackathon. A devlog entry costs two
minutes per PR; a decision record is written while the alternative is still
remembered — which is also exactly what the judges asked for in the
decision-log deliverable. For AI agents the payoff is larger still: a new
agent contextualizes itself by reading one file instead of reverse-engineering
the repo.

## Does not solve

Documentation quality. The guard checks presence, not truth — a workstream
can write a useless entry and pass. Reviews still have to read.

## Consequences for contracts

None (process only). The guard treats `contracts/` as code with a higher bar:
contract changes additionally require a decision record in the same PR.
