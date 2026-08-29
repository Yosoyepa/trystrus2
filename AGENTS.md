# AGENTS.md — orientation for any agent (AI or human) working on Aval

You are about to build inside a four-developer parallel effort with frozen
contracts. This file is the fastest path from zero to useful. Read it fully
before touching anything. It is short on purpose.

## What Aval is

A trust layer for purchases made by AI agents (NextWave Hackathon 2026,
challenge "The buyer who isn't human", Yuno × Nauta). A person signs a
**mandate** (SD-JWT, RFC 9901) saying what their agent may buy, up to how much,
until when, with which payment method — a vaulted PayPal token, never a raw
card. The agent proposes; a **deterministic gate** decides; the merchant
verifies the cryptography itself before charging; every decision lands in a
**hash-chained audit log**. Live revocation must fail the next attempt in
≤ 2 s. Judges will operate it live and try to break it.

Full story: [`aval/README.md`](aval/README.md) · shared concepts:
[`aval/docs/FOUNDATION.md`](aval/docs/FOUNDATION.md).

## Read order (by role)

| You are working on… | Read, in order |
|---|---|
| Anything (start here) | `aval/README.md` → this file → `aval/DECISIONS.md` |
| kernel (mandates, gate, verify, ledger) | + `aval/docs/PLAN.md` §5 (ADRs), `aval/contracts/schemas.md` |
| agent or merchant | + `aval/contracts/api.yaml`, `schemas.md` §1–2 (mandate + intent crypto) |
| web frontend | + `aval/contracts/api.yaml` (your ONLY dependency — types are generated from it) |
| Planning / who builds what | `aval/docs/PLAN-PARALELO.md` |

The two `docs/PLAN*.md` files are in Spanish (team working language); the repo
and contracts are in English. If a plan and a contract disagree, **the contract
wins** and the plan needs a PR.

## Repo map (and the naming map to the plans)

```
aval/
├── DECISIONS.md        # graded deliverable: 15 decisions, chosen/rejected/why/limits
├── docs/
│   ├── FOUNDATION.md   # shared concepts, flow, control tower (the "what")
│   ├── PLAN.md         # master plan: research, architecture, ADRs, gates, TDD (the "why")
│   ├── PLAN-PARALELO.md# 4 workstreams, milestones, parallel rules (the "who/when")
│   └── architecture.html, fig*.png
├── contracts/          # FROZEN INTERFACES — the only shared surface
│   ├── api.yaml        # OpenAPI 3.1: kernel + merchant endpoints, DTOs, error codes
│   └── schemas.md      # SD-JWT claims, canonical intent (JCS), events, DDL, Python interfaces
├── kernel/             # [Dev A + B] mandate registry, passkeys, gate, verify, saga, ledger, SSE
├── agent/              # [Dev C] LangGraph agent, intent signer, watcher job, Presidio, injection suite
├── merchant/           # [Dev C] VuelaYa catalog + MCP tools, checkout, PayPal adapter, webhooks
└── web/                # [Dev D] React SPA: buyer console, judge/auditor console (control tower),
                        #         merchant console + Telegram bot logic + GCP infra/
```

Naming map: what the plans call `api` is `kernel/` here. The plans' "UI Auditor"
is the control tower. Product working name in older notes ("TrustChannel") is
Aval. Deployables: `web`, `kernel`, `agent` (+ watcher Cloud Run job),
`merchant` — all on Cloud Run, region `southamerica-east1`.

## Ownership (4 parallel workstreams)

| Dev | Owns | Mission |
|---|---|---|
| A | `kernel` mandates + identity: SD-JWT issuance, JWKS, passkey ceremony, state machine, revocation, escalations API | Only a real human with a passkey creates/limits/revokes spending power |
| B | `kernel` decision + evidence: policy gate, verify endpoint (atomic reservation), purchase saga, hash-chained ledger, KMS roots, outbox/SSE | Nothing out-of-mandate ever passes; every decision leaves verifiable evidence |
| C | `agent` + `merchant`: LangGraph graph, signed intents, watcher, Presidio, injection suite; VuelaYa catalog/checkout, PayPal adapter, webhooks | The agent discovers and buys; the merchant verifies before charging; money moves by PayPal without the agent ever seeing the instrument |
| D | `web` (all three consoles) + Telegram bot + `infra/` (bootstrap, CI/CD, domain, secrets) | Humans, judges and auditors operate everything; the system stays deployed and reproducible |

Do not edit another workstream's module without their review. Contracts are
community property: change them by PR with a version bump, updating mocks and
generated types in the same commit. Mocks must behave like the real service —
a mock that approves everything is forbidden.

## Non-negotiable rules (the ones that fail the demo if broken)

1. **No model inside the enforcement path.** The gate is deterministic code;
   the LLM proposes, it never disposes (DECISIONS #1, #5).
2. **One path to money.** The agent has no route to any payment API that does
   not pass through the gate + verify. If you are adding a way around it, you
   are breaking the project (DECISIONS #6).
3. **Fail closed.** Escalation timeout (120 s) denies; unknown states deny;
   silence never approves (DECISIONS #13).
4. **Never touch a card.** Payment method = opaque `payment_method_ref`
   (PayPal payment token). PAN/CVV nowhere, never (DECISIONS #8).
5. **Revocation is synchronous.** Verify reads mandate state inside the
   charging transaction and DELETEs the rail token on revoke (DECISIONS #4).
6. **Append-only evidence.** Business change and audit event commit in the same
   transaction; roots are signed with KMS and witnessed externally (DECISIONS #7, #10).
7. **Log every decision you make** in `aval/DECISIONS.md` when you make it —
   chosen, rejected, why, and what it still does not solve.

## Environment facts (do not rediscover these)

- **GCP**: everything on Cloud Run, `southamerica-east1`; Cloud SQL Postgres
  `db-f1-micro` via unix socket (no public IP); keys in Secret Manager + KMS
  (`EC_SIGN_ED25519` — that exact algorithm name).
- **Domain**: passkeys require a real registrable domain (they fail on
  `*.run.app` — public suffix list). Subdomains: `app.` (web), `api.`
  (kernel), `merchant.`.
- **PayPal sandbox**: REST direct with httpx (official Python SDKs are
  deprecated). Vault feature checkbox must be on in the sandbox app settings.
  Amounts in USD only. Webhooks verified via `/v1/notifications/verify-webhook-signature`.
- **LLM**: Vertex AI Gemini (paid, covered by trial credits) or OpenAI. The
  Gemini API free tier (~20 req/day) is NOT an option. The gate is model-agnostic.
- **Events**: Postgres outbox + `FOR UPDATE SKIP LOCKED` polling. No broker.
  `LISTEN/NOTIFY` does not survive Cloud Run scaling.

## Status (as of this commit)

Decided and documented: architecture, crypto formats, contracts v1.0, decision
log (15 entries), workstreams and milestones. Next: the M0 freeze session
(see `aval/docs/PLAN-PARALELO.md` §11) — scaffold the services, stand up the
mocks in `contracts/mocks/`, generate TypeScript types from `api.yaml`, buy the
domain, run the PayPal smoke test. Until M0 is green, no workstream starts
building.
