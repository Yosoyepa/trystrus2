# 0020 — Python deployables live under `src/`, and the docs guard follows them

Date: 2026-08-29 · Status: accepted
Workstream: 3 (affects all)
Supersedes: the Python half of the repo map in AGENTS.md and PLAN-PARALELO §8

## Context

PLAN-PARALELO §8 and AGENTS.md place the services at `aval/kernel/`,
`aval/agent/`, `aval/merchant/`. The scaffold commit created `src/api/` at the
repo root instead, which is the ordinary Python layout and what the team
started from.

The mismatch is not cosmetic. `scripts/docs-guard.sh` recognises code by path:

    ^((aval/)?(kernel|agent|merchant|web|packages|services|infra)/).*\.(py|...)$

Code under `src/` matched nothing, so **CI would have stopped requiring devlog
entries** — silently disabling decision #17 for every Python change in the repo.

## Chose

Python deployables live at the repo root under `src/`:

    src/trustlib/   common — models, canonical JSON, JOSE, SD-JWT, AP2, Protocols
    src/api/        kernel: mandates, passkeys, escalations [3] · gate, verify [2]
    src/merchant/   VuelaYa catalog, MCP tools, checkout, Checkout JWT [3]
    src/yuno_sim/   Yuno-style AP2 payment orchestrator, simulated [3]
    src/agent/      own-graph agent, intent signer, watcher [1]

`aval/` keeps what it already holds: contracts, docs, decisions, devlogs, and
`web/` (Dev 4's own toolchain, unaffected).

The guard's pattern gains a `src/` alternative in the same commit, so the
documentation obligation survives the move. Its failure message also stops
naming the pre-decision-#19 devlogs (`A|B|C1|C2|C3|D`) and names `dev1`–`dev4`.

## Rejected

* **Move the code to `aval/kernel/` etc.** Matches the written plan, but fights
  both the existing scaffold and every Python tool's default expectation
  (`pythonpath`, editable installs, import roots).
* **Leave the guard alone.** Would have left decision #17 unenforced for Python.
  A guard that silently stops guarding is worse than no guard.
* **Nest `src/` inside `aval/`.** Splits the Python root from the project root
  for no gain; `pyproject.toml` lives at the repo root regardless.

## Why

The guard is the load-bearing part, not the folder name. Decision #17 exists
because four people and their agents build asynchronously and the failure mode
is lost context; an unenforced convention dies on day 2. Moving the code was the
cheap half; keeping the obligation attached to it was the point.

Ownership does not change: `src/` folders map to the same owners as the
directories the plans named, and CODEOWNERS follows the new paths.

## Does not solve

**Two documents now describe the layout, and only one is generated.** If someone
edits PLAN-PARALELO §8 without touching AGENTS.md they will drift again. Nothing
enforces the repo map itself — the guard checks that documentation was *touched*,
not that it is *true* (decision #17's own stated limit).

**Dev 4's `web/` still lives under `aval/`,** so the repo has two roots. Deliberate:
the frontend has its own toolchain and never imports Python.

## Consequences for contracts

* `scripts/docs-guard.sh` — pattern extended with `^src/`; message updated.
* `AGENTS.md` — repo map rewritten to show both roots.
* `aval/contracts/` — untouched.
* CI path-based triggers (PLAN-PARALELO §6.5) will need `src/**` prefixes when
  Dev 4 wires the workflows.
