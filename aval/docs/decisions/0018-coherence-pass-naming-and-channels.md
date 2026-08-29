# 0018 — Coherence pass: naming and channels aligned to the decided build

Date: 2026-08-29 · Status: accepted · Workstream: all
Follows: #8 (rail), #13 (channels), #16 (own graph), #17 (documentation
protocol).

## Context

After decisions 16–17 landed, a sweep found the foundation documents still
telling the pre-decision story: `FOUNDATION.md` and `architecture.html`
presented Slack as a buyer channel (rejected in #13), closed with "the
mandate object is not decided yet" (it is frozen in `contracts/schemas.md`
§1), said the platform keeps the card (the instrument lives tokenized at the
PayPal rail per #8), and the contract example still carried a lowercase
`api.trustchannel.example` issuer that the Aval rename had missed. The
sources footer of the master plan pointed at a dead `docs/adr/` path.

## Chose

Align every document to the decided build:

- Channels read web / Telegram (primary) / WhatsApp (secondary), matching #13.
- "Not decided yet" and "Still open" sections became "Since decided", each
  closed with the decision number and contract section that resolves it.
- FOUNDATION gained a whiteboard-component → build mapping (which service and
  decision each box became).
- `contracts/schemas.md` §1 issuer example is now `api.aval.example`.
- The plan's sources footer points at `docs/decisions/`.

## Rejected

Leaving the foundation docs as untouched historical artifacts — `README.md`
sends every newcomer and every AI agent to them first; a doc that says "not
decided yet" about a frozen contract is an active hazard, not history.
Also rejected: rewriting the whiteboard voice entirely — those documents are
the bridge from the original idea to the build; annotating beats replacing.

## Why

In a repo where agents (human and AI) contextualize themselves from the docs,
coherence is load-bearing. This record exists because the docs-guard demands
one for any contract change — including a one-line example rename — and that
is the protocol working as designed, not an exception to it.

## Does not solve

`docs/fig1-3.png` still show the original whiteboard wording; they are kept
as artifacts of the starting point, not regenerated.

## Consequences for contracts

- `contracts/schemas.md` §1: issuer example value only (`api.aval.example`)
  — no format, claim or verifier change.
