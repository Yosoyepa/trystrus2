# Aval

A trust layer for purchases made by AI agents.

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
- `docs/PLAN.md` is the researched master plan (architecture, ADRs, gates, test strategy, ~140 sources).
- `docs/PLAN-PARALELO.md` is the parallel build plan (4 workstreams, contracts, milestones).
- `contracts/` holds the frozen interfaces — `api.yaml` (OpenAPI) and `schemas.md` (crypto formats, events, DDL). If code and contract disagree, the contract wins.

## Repo layout

    docs/            foundation, architecture, diagrams, master plan, parallel plan
    contracts/       frozen interfaces: OpenAPI + schemas + (soon) mocks and fixtures
    DECISIONS.md     what we chose, what we rejected, why

Everything below is to be filled in as it gets built.

    kernel/          mandate schema, signing, rules engine, log
    merchant/        VuelaYa catalog, checkout, verification endpoint
    agent/           purchasing agent, price watcher, escalation
    web/             buyer wallet, judge console, merchant console, control tower

## Running it

To be written. Target: one command, no external services, no network dependency
between a fresh clone and a working demo.

## What the challenge asks for

- [ ] A person creates a mandate without handing over the raw card
- [ ] The merchant verifies the mandate before accepting
- [ ] An end to end purchase inside the mandate
- [ ] Over the limit, wrong category, or expired: refused or escalated, never silently approved
- [ ] Live revocation: revoked, and the next attempt fails
- [ ] An impersonated agent is handled
- [ ] Three views: the person's record, the merchant's verification, the auditor's trail
- [ ] Every decision leaves an auditable trail
- [ ] The judges can operate it live without us touching anything
- [ ] Slides, demo, public repo, architecture diagram, decision log

## Team

Four parallel workstreams; contracts are the only shared surface. Names TBD.

| Area | Owner |
|---|---|
| Kernel: mandate, signing, rules, log | Dev A (identity: mandates, passkeys, SD-JWT, revocation, escalations) + Dev B (decision: gate, verify, saga, ledger, events) |
| Merchant: catalog, checkout, verification | Dev C (also owns the PayPal adapter and webhooks) |
| Agent: discovery, proposals, escalation | Dev C |
| Web and story: consoles, bot, slides, diagram | Dev D (also owns GCP infra and CI/CD) |
