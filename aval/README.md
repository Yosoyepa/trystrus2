# TryTrust

`trytrust.lat` — a trust layer for purchases made by AI agents.

> Renamed from Aval, 29 Aug 2026. The `aval/` directory name and the frozen
> contract paths are unchanged so nobody's branch breaks mid-hackathon.

NextWave Hackathon 2026, challenge 05-01, "The buyer who isn't human".
Yuno x Nauta, Bogota, 29-30 August 2026.

## The problem

A person tells an agent to buy something and the agent buys it. The merchant
cannot check that the person agreed, and the person cannot prove afterwards what
they agreed to. So merchants either block agents and lose real sales, or let them
through as people and absorb the fraud.

The missing piece is a permission that can be written down, checked by a stranger,
and taken away.

## What this does

A person signs a mandate saying what their agent may buy, up to how much, until
when, and with which payment method. The agent searches and proposes. A rules
engine checks the proposal against the mandate and either pays, asks the person,
or refuses. Every decision goes into an append-only log that the person, the
merchant and an auditor can all read.

The agent never holds the card, and it never calls the merchant's payment API.

## Read these first

- `../AGENTS.md` is the orientation for anyone (or any agent) joining mid-flight.
- `docs/FOUNDATION.md` is the shared starting point. Read it before writing code.
- `docs/architecture.html` has the three diagrams and every term defined.
- `DECISIONS.md` is a graded deliverable. Add an entry when you make a call, not at the end.
- `docs/decisions/` holds the full decision records (one numbered file per choice); `DECISIONS.md` is the index.
- `docs/devlogs/` is the per-workstream build log. Read yours before starting a task; append to it in every PR — CI rejects code changes without a devlog entry.
- `docs/PLAN.md` is the researched master plan (architecture, ADRs, gates, test strategy, ~140 sources).
- `docs/PLAN-PARALELO.md` is the parallel build plan (4 workstreams, contracts, milestones).
- `contracts/` holds the frozen interfaces — `api.yaml` (OpenAPI) and `schemas.md` (crypto formats, events, DDL). If code and contract disagree, the contract wins.

## Repo layout

    docs/            foundation, architecture, diagrams, master plan, parallel plan
    docs/decisions/  full decision records (one numbered file per choice + TEMPLATE)
    docs/devlogs/    one append-only build log per workstream — the anti-duplication radio
    contracts/       frozen interfaces: OpenAPI + schemas + (soon) mocks and fixtures
    DECISIONS.md     decision index: what we chose, what we rejected, why

Everything below is to be filled in as it gets built.

    kernel/          mandate schema, signing, rules engine, log
    merchant/        VuelaYa catalog, checkout, verification endpoint
    agent/           purchasing agent, price watcher, escalation
    web/             buyer wallet, judge console, merchant console, control tower

## Running it

The agent lane runs today, from a clean clone, with no external service:

    uv run python -m src.agent.cli reset && uv run python -m src.agent.cli seed
    uv run python -m src.agent.cli demo      # the whole story
    uv run python -m src.agent.cli chat      # talk to it
    uv run python -m src.agent.tests         # 20 property checks

Details: [`src/agent/README.md`](src/agent/README.md).
Everything the system must conserve: [`docs/PROPERTIES.md`](docs/PROPERTIES.md).
Where we sit among AP2, ACP and the rest: [`docs/PROTOCOLS.md`](docs/PROTOCOLS.md).

## What the challenge asks for

- [x] A person creates a mandate without handing over the raw card
- [x] The merchant verifies the mandate before accepting
- [x] An end to end purchase inside the mandate
- [x] Over the limit, wrong category, or expired: refused or escalated, never silently approved
- [x] Live revocation: revoked, and the next attempt fails
- [x] An impersonated agent is handled
- [ ] Three views: the person's record, the merchant's verification, the auditor's trail
- [x] Every decision leaves an auditable trail
- [ ] The judges can operate it live without us touching anything
- [ ] Slides, demo, public repo, architecture diagram, decision log

## Team

Four capability lanes; contracts are the only shared surface. Names TBD.

| Area | Owner |
|---|---|
| Agentic: agent graph, watcher, injection suite | Dev 1 |
| Fraud, contracts, idempotency: gate, verify, saga, ledger | Dev 2 |
| API backend: mandates, passkeys, merchant, PayPal rail | Dev 3 |
| Front and platform: consoles, bot, GCP infra, CI/CD | Dev 4 |

Workstreams are cut by capability (decision #19,
`docs/decisions/0019-workstreams-cut-by-capability.md`). The old C1/C2/C3
subdivision dissolved into it: brain → Dev 1, money and store → Dev 3.
Contracts, milestones and tests did not change.
