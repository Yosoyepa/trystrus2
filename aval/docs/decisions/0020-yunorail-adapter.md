# 0020 — Yuno behind PaymentRail: mock in the demo, real integration out of scope

Date: 2026-08-29 · Status: accepted · Workstream: 3 (proposed) / all
Supersedes: none (extends decision #8 — PayPal stays the real rail)

## Context

The fraud research (dev3 committee, D-06/D-07) found Yuno is integrable and
strategic for a Yuno-sponsored hackathon (risk conditions, `fraud_screening`,
3DS with `liability_shift`, stored credentials CIT/MIT, HMAC webhooks,
`X-Idempotency-Key`, MCP server). But we have no sandbox credentials
(`account_id` + keys), and the `PaymentRail` protocol has two gaps Yuno
exposes: no `get_status` for non-terminal states (Yuno is frequently async)
and `open_dispute` does not map (disputes are inbound; merchants only respond
evidence).

## Chose

- Extend `PaymentRail` with `get_status(external_ref) -> Receipt` and
  `respond_dispute(dispute_id, evidence) -> DisputeRef`; deprecate
  `open_dispute` (PayPal sandbox simulation only).
- Add `YunoMockRail`: a faithful mock of Yuno's verified contract (auth
  headers, `X-Idempotency-Key` four behaviors, reduced-faithful state machine
  `CREATED → PENDING/{IN_PROCESS, PENDING_FRAUD_REVIEW, AUTHORIZED} →
  SUCCEEDED|DECLINED/FRAUD_DECLINED|ERROR/TIMEOUT`, webhook v2 payload with
  real HMAC + compressed retries, injectable `mock_mode`).
- Rail selection by env: `AVAL_RAIL=paypal|yuno_mock`. **The demo runs PayPal
  as the real rail and `yuno_mock` as the sponsor story; the real Yuno
  integration (research task F2.2) is descarted for this event** — no
  credential dependency on the critical path.
- `idempotency_key` sent to any rail = `HMAC(jti)` — derived, never
  caller-invented (R-IDEM); persisted 45 days locally.

## Rejected

- Real Yuno integration now: no sandbox credentials exist and the 48 h gate
  before the demo cannot be met — it would put the sponsor's account
  provisioning on the critical path.
- Yuno as primary rail with PayPal fallback: same credential problem, plus
  enrollment needs PCI or the Testing Gateway — untestable in time.
- Ignoring Yuno entirely: it is the sponsor; a faithful mock plus the mapped
  adapter design keeps the story honest ("we built against the documented
  contract") without betting the demo on it.

## Why

The `PaymentRail` protocol was designed exactly for this swap: the kernel
never knows who charges. The mock is built from the real documented contract
(docs.y.uno — every endpoint, header and state verified during research), so
the adapter work is not throwaway: when credentials exist, `YunoRail` real is
a configuration change, not a redesign. And the two protocol gaps Yuno
exposed are real gaps for PayPal too (async captures exist there as well;
disputes are inbound on both rails).

## Does not solve

We will not be able to claim "running on Yuno" — only "integrated against
Yuno's documented contract, demonstrated via a faithful mock". Real-credential
validation of the doc inconsistencies found (auth header casing, production
server URL, idempotency TTL) remains untested. Kept as Fase 4 roadmap.

## Consequences for contracts

`schemas.md` §3 (`PaymentRail` +2 methods, `open_dispute` deprecated,
AVAL_RAIL note), §4 (+`webhook.rejected` event), §6 (`webhook_archive` DDL,
`idempotency_keys.derived_from`/`expires_at`), §8 (mock-yuno row), header
bumped to v1.1. Realization: plan F2.1 (mock, 4 h); F2.2 descarted.
