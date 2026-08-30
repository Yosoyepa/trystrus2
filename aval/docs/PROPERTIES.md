# The property register

Every property TryTrust must conserve, named. Cite the IDs in code comments,
decision records, PR descriptions and the demo script. They roll up to the
objectives O1–O8 in [`PLAN.md`](PLAN.md).

`†` = introduced with the agent/console work (decision #20).
**test** names the check in `src/agent/tests.py`; run them with
`uv run python -m src.agent.tests`.

## S — Safety (enforcement)

| ID | Property | Enforced at | Test |
|---|---|---|---|
| S1 | No model runs in the enforcement path; same input → same answer | `kernel.gate` | ✅ 200x determinism |
| S2 | Exactly one path to money: gate → verify → checkout. No agent→rail edge | architecture | ✅ no tool reaches money; watch fires through the gate |
| S3 | Fail closed — unknown state denies, silence never approves | gate, escalation | ✅ timeout denies |
| S4 † | Memory and ontology feed `propose` only; neither reaches the gate | `graph.node_gate` | ✅ hostile ontology changes nothing |
| S5 | Conditions are pure JsonLogic over `{offer.*, now}`; no custom functions | `jsonlogic` | ✅ `exec`/`http`/`eval` refused |
| S6 | The agent cannot name a price — `request_purchase` takes no amount; `intent.amount == offer.price` | MCP §10, `kernel.verify` | ✅ AMOUNT_MISMATCH |
| S7 | An approval authorises a retry, never a bypass — the gate re-runs | `escalation.resolve` | ✅ revoke mid-escalation |
| S8 | The merchant refuses to charge without an APPROVED verify | `merchant.checkout_charge` | covered by S7 |
| S9 | Mocks apply the real decision table; "approve everything" is forbidden | `src/agent/mocks/` | by construction |

## C — Cryptography and identity

| ID | Property | Enforced at | Test |
|---|---|---|---|
| C1 | The mandate is signed; one mutated byte → rejected | `mandate.verify_token` | ✅ |
| C2 | Agent identity is an Ed25519 keypair, bound in `cnf.jwk` | `mandate.issue` | ✅ |
| C3 | Every purchase carries a detached JWS over canonical JSON (RFC 8785) | `crypto.jws` | ✅ |
| C4 | The agent proves possession at presentation, bound to the verifier's nonce | `kernel.verify` | partial — KB nonce is the intent nonce |
| C5 | Verification order is fixed: signature → possession → freshness → uniqueness → state | `kernel.verify` | ✅ |
| C6 | Intent freshness: `exp − iat ≤ 120 s` | `kernel.verify` | ✅ |
| C7 | Replay protection: `jti` and `nonce` (≥128 bits) unique | UNIQUE + check | ✅ |
| C8 | The merchant verifies offline against JWKS — it never has to trust our answer | `merchant.checkout_charge` | ✅ via C1 |
| C9 | JWKS rotation publishes current + previous, `kid`-versioned, 24 h grace | `mandate.jwks` | **not yet** — one key today |
| C10 | Issuer keys are files locally, Secret Manager + KMS in GCP | `crypto.keys` | **local only** |

## M — Money and consistency

| ID | Property | Enforced at | Test |
|---|---|---|---|
| M1 | The same request twice buys once | `idempotency_keys` | ✅ |
| M2 | Atomic reservation by compare-and-swap; zero rows means refuse | `kernel.reserve_chain` | ✅ no double-spend |
| M3 | `verify` is the only writer of `reserved_amount` / `spent_total` / `txn_count` | convention + review | by construction |
| M4 | No budget is reserved while an escalation is pending | `kernel.submit_purchase` | ✅ via S7 |
| M5 | Resume is idempotent — resolving twice charges once | `escalation.resolve` | ✅ |
| M6 | Every failure compensates: `rejected` or `compensated`, reservation released | `kernel._compensate` | ✅ via S7 |
| M7 | Amounts are fixed 2-decimal strings; floats are refused at serialisation | `crypto.canonical` | by construction |
| M8 | Revocation fails the next attempt twice — our state and the rail token | `mandate.revoke` | ✅ |
| M9 | Revocation is re-read inside the charging path (TOCTOU closed) | `kernel._charge` | ✅ via S7 |
| M10 | Revocation takes effect in ≤ 2 s | measured | ✅ ~0.05 s locally |

## E — Evidence and traceability

| ID | Property | Enforced at | Test |
|---|---|---|---|
| E1 | `audit_events` is append-only — the database refuses UPDATE and DELETE | Postgres `plpgsql` triggers | ✅ |
| E2 | Hash chain per mandate: mutate, delete or reorder one event and that chain stops replaying | `audit.verify_all` | ✅ both attacks |
| E3 | One signed root over every chain head, witnessed outside the database | `audit.checkpoint` | ✅ local file; KMS + GCS in GCP |
| E4 | Business change and audit event commit in one transaction | `audit.append` | by construction |
| E5 † | One log, not two — the agent writes to the same chain | `graph._save` | ✅ via E7 |
| E6 | Every outcome names the check that produced it (`ReasonCode`) | gate, verify | ✅ throughout |
| E7 † | The agent's trajectory is evidence — every node transition is an event | `graph._save` | ✅ |
| E8 † | Each run pins the `agent_version` it ran with | `agent_runs` | ✅ publish mid-run |
| E9 † | Config edits are versioned, never destructive | `agent_versions` + trigger | ✅ append-only |
| E10 | PII is scrubbed before anything enters the chain | `scrub` | ✅ |
| E11 | Non-repudiation both ways | passkey + merchant record | partial — passkey mocked |
| E12 † | Every config edit records actor, timestamp and reason | `registry.publish_version` | ✅ |

## H — Human in the loop

| ID | Property | Enforced at | Test |
|---|---|---|---|
| H1 | Escalation is a stop, not a notification | `graph.node_await_human` | ✅ |
| H2 | 120 s timeout → auto-deny, fail closed | `escalation.expire` | ✅ |
| H3 | The escalation shows a readable diff | `kernel.gate` diff | ✅ shown in chat |
| H4 | Only check 3 may escalate; forged agent / dead mandate refuse outright | `kernel.gate` | ✅ |
| H5 † | Every agent has a named approver — escalations go to a person | `agents.approver_id` | ✅ |
| H6 | `sticky` issues a derived mini-mandate; it cannot exceed its parent | `escalation._retry_through_gate` | ✅ child debits parent |

## K — Configuration and knowledge †

| ID | Property | Enforced at | Test |
|---|---|---|---|
| K1 † | Ontology is unsigned advice; the mandate is signed law. Editing an ontology can never widen a limit | architecture | ✅ **the demo test** |
| K2 † | Config lives in the database and is queryable; files are import/export only | `registry` | ✅ |
| K3 † | A running run keeps its pinned version; no brain swap mid-flight | `graph.start` | ✅ |
| K4 † | Every agent has a named owner, approver and auditor | `agents` | ✅ |
| K5 † | The ontology is an injection surface — fenced like merchant text | `llm.fence` | ✅ via S4 |

## G — Guardrails and blast radius †

Defence in depth. G0 is the one that stops the attack; the rest assume it failed.

| ID | Property | Enforced at | Test |
|---|---|---|---|
| G0 † | The model has no verb for scheduling, spending or self-modification — three read/submit tools, no `amount` | MCP §10 | ✅ no tool reaches money |
| G1 † | A polling interval below the floor is refused, not silently clamped | `limits.guard_watch_interval` | ✅ |
| G2 † | Watches are capped per mandate and system-wide | `limits.guard_watch_count` | ✅ |
| G3 † | Only one watcher tick runs at a time, across processes and machines | `limits.single_flight` (Postgres advisory locks) | ✅ |
| G4 † | External calls are rate limited per agent; a throttled run ends `denied`, never `captured` | `limits.take` | ✅ |
| G5 † | Escalations per approver per hour are capped | `limits.guard_escalation` | ✅ |
| G6 † | Every run has both a step budget and a wall clock; either fails it closed | `graph.run_until_pause` | ✅ |
| G7 † | Catalog and ontology text entering the prompt are bounded | `limits.clamp_offers` / `clamp_text` | ✅ |
| G8 † | Spend counters live in the database — a restart does not reset an attacker's budget | `limits.bump` | ✅ |
| G9 † | The agent cannot call a host that is not on the allowlist, and refusals are logged | `net.check` | ✅ |
| G9 † | The agent process cannot read `$HOME`, secrets, or write outside `var/` | `deploy/sandbox.sh` | measured |
| G10 † | The agent process cannot modify its own enforcement code | sandbox read-only bind | measured |

Guardrail trips are audited (`watch.throttled`, node events with
`guardrail: true`), never silent. Full write-up: [`../deploy/SECURITY.md`](../deploy/SECURITY.md).

## A — Console authentication †

| ID | Property | Enforced at | Test |
|---|---|---|---|
| A1 † | The console is not anonymous: no token, no change | `auth.require` | ✅ |
| A2 † | The role grants the capability; attachment to the agent grants the instance | `auth.authorize` | ✅ |
| A3 † | Only the named approver may resolve that agent's escalation | `auth.authorize` | ✅ |
| A4 † | Tokens are stored hashed; a leaked database is not a set of credentials | `auth.hash_token` | ✅ |
| A5 † | Every refused console action is on the record | `auth.require` | ✅ |
| A6 † | No console credential can widen a spending limit — that authority is the passkey's | architecture | ✅ via S4/K1 |

## X — Extension points †

| ID | Property | Enforced at | Test |
|---|---|---|---|
| X1 † | A tool may declare `read` or `submit` and nothing else; the constructor refuses the rest | `ports.base.Tool` | ✅ |
| X2 † | A new merchant appears in search, and the mandate's scope still decides who may buy | `ports.base.search_all` | ✅ |
| X3 † | A merchant that fails to settle compensates; no reservation leaks | `kernel._compensate` | ✅ |
| X4 † | Against a live merchant MCP: buy through the gate, and refuse its settling tool | `ports.merchants_mcp` | ✅ live |
| X5 † | Settlement lives on the merchant port, never in the tool registry — a capability the agent cannot name is one it cannot be talked into using | architecture | ✅ via X1 |

## P — Platform and process

| ID | Property | Status |
|---|---|---|
| P1 | Reproducible from a clean checkout | ✅ `reset` → `seed` → `demo`, no external services |
| P2 | Real registrable domain with TLS (passkeys fail on `*.run.app`) | **open** — `trytrust.lat` does not resolve yet |
| P3 | No cold starts in the demo window | not yet deployed |
| P4 | Judges operate it live without us touching anything | ✅ `chat` + `demo` |
| P5 | No code PR without a devlog; no contract change without a decision record | CI `docs-guard.sh` |
| P6 † | Network egress allowlist for the agent process | ✅ in-process (`net.check`, test G9); VPC rules remain the production answer |
| P7 † | Authentication on the console | ✅ bearer tokens, hashed (A1–A5); no rotation or expiry yet |

## What is still mocked

| Mocked | Lane | Real in this build |
|---|---|---|
| Passkey ceremony | Dev 3 | the mandate signature and the claim shape; `signed_with` records the intent |
| SD-JWT selective disclosure | Dev 3 | signing, key binding to `cnf.jwk`, offline verification |
| MCP transport | Dev 3 | the three-tool contract and its boundary rules |
| PayPal REST | Dev 3 | vaulted-token model, capture idempotency, token DELETE, dispute object |
| Kernel gate/verify service | Dev 2 | the decision table, atomic reservation, the saga, the chain |
| Cloud Run deploy | Dev 4 | Postgres is real in dev and prod; the deploy is not done |

## Merchants: real, and one thing outstanding

The agent buys from `vuela-ya` and `mami` over their own MCP servers. Both
expose a `pay` tool that settles with no mandate and no signature — an agent
reaching those endpoints can buy unauthorised. Ours does not: `pay` is recorded
as refused and settlement goes through `MerchantPort.settle()`, which only the
kernel calls after the gate approved. Gating it merchant-side is
`aval/docs/MCP-HANDOFF.md`, and it is their repo's call.
