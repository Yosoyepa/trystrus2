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
| Any workstream, before each task | + the last 3 entries of your devlog: `aval/docs/devlogs/<A\|B\|C1\|C2\|C3\|D>.md` |
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
├── DECISIONS.md        # graded deliverable: decision INDEX (full records in docs/decisions/)
├── docs/
│   ├── FOUNDATION.md   # shared concepts, flow, control tower (the "what")
│   ├── PLAN.md         # master plan: research, architecture, ADRs, gates, TDD (the "why")
│   ├── PLAN-PARALELO.md# workstreams, milestones, parallel rules (the "who/when")
│   ├── decisions/      # full decision records, one numbered file per choice (+ TEMPLATE)
│   ├── devlogs/        # one append-only build log per workstream (A, B, C1, C2, C3, D)
│   └── architecture.html, fig*.png
├── contracts/          # FROZEN INTERFACES — the only shared surface
│   ├── api.yaml        # OpenAPI 3.1: kernel + merchant endpoints, DTOs, error codes
│   └── schemas.md      # SD-JWT claims, canonical intent (JCS), events, DDL, Python interfaces
├── kernel/             # [Dev A + B] mandate registry, passkeys, gate, verify, saga, ledger, SSE
├── agent/              # [Dev C3] own-graph agent, intent signer, watcher job, Presidio, injection suite
├── merchant/           # [Dev C1+C2] VuelaYa catalog + MCP tools, checkout, PayPal adapter, webhooks
└── web/                # [Dev D] React SPA: buyer console, judge/auditor console (control tower),
                        #         merchant console + Telegram bot logic + GCP infra/

(repo root: scripts/docs-guard.sh = CI documentation gate ·
 .github/workflows/docs-guard.yml = the PR check that runs it)
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
| C1 | `merchant` rail: PayPal adapter (setup/payment tokens, capture with `vault_id`, disputes, DELETE), incoming webhooks | Money moves by PayPal without anyone touching the instrument; the rail obeys revocation |
| C2 | `merchant` store: VuelaYa catalog + MCP tools, checkout verification | The merchant verifies the mandate itself before charging |
| C3 | `agent`: own graph orchestrator (`await_human` + `agent_runs` checkpointing, decision #16), signed intents (JCS/EdDSA), watcher job, Presidio, injection suite | The agent discovers and proposes; it structurally cannot pay outside the gate |
| D | `web` (all three consoles) + Telegram bot + `infra/` (bootstrap, CI/CD, domain, secrets) | Humans, judges and auditors operate everything; the system stays deployed and reproducible |

Dev C owns all three sub-missions by default, in the order C1 → C2 → C3 (see
`aval/docs/PLAN-PARALELO.md` §3.1). Each sub-mission is self-contained — its
boundaries are existing contracts (`PaymentRail` interface for C1↔C2, the MCP
tools contract in `contracts/schemas.md` §10 for C2↔C3) — so they can be split
across people or assigned to separate agents without extra synchronization.
The detachable one if someone is overloaded: C2 (Dev A can absorb it after M1).

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
7. **Document as you go, not at the end.** Every decision gets a record in
   `aval/docs/decisions/` (plus a short index entry in `aval/DECISIONS.md`)
   when you make it — chosen, rejected, why, what it still does not solve.
   Every PR that changes code appends an entry to your workstream's devlog
   (`aval/docs/devlogs/`). CI rejects a PR that skips either
   (`scripts/docs-guard.sh`, decision #17).

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
- **No agent framework**: the orchestrator is our own ~150-line graph
  (decision #16) — explicit nodes, LLM only in `propose`, checkpointing in
  `agent_runs`, `await_human` resumed by escalation resolution. Do not add
  langgraph / openai-agents / crewai dependencies.
- **Events**: Postgres outbox + `FOR UPDATE SKIP LOCKED` polling. No broker.
  `LISTEN/NOTIFY` does not survive Cloud Run scaling.

## Status (as of this commit)

Decided and documented: architecture, crypto formats, contracts v1.0, decision
log (17 entries — full records in `docs/decisions/`), workstreams and
milestones, own-graph agent orchestrator (#16), documentation protocol with
CI guard (#17). Next: the M0 freeze session
(see `aval/docs/PLAN-PARALELO.md` §11) — scaffold the services, stand up the
mocks in `contracts/mocks/`, generate TypeScript types from `api.yaml`, buy the
domain, run the PayPal smoke test. Until M0 is green, no workstream starts
building.
