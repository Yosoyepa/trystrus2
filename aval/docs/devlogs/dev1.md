# Devlog — Dev 1 · Agentic (agent service + watcher job)

Mission: an agent that discovers and proposes — structurally unable to pay
outside the gate, resilient to prompt injection. Scope and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-30 — explicit offer selection survives an unavailable LLM

- **Why:** the production-integration invariant run exposed that S4/K1 passed
  only when a live model interpreted `buy offer ofr_cor_300`. With no key or a
  model outage, the documented deterministic fallback ignored the explicit ID,
  chose the globally cheapest catalog row and denied for the wrong reason.
- **Built:** `_propose_fallback` now recognizes an exact catalog `offer_id` in
  the buyer request before applying cheapest-match selection. The model still
  has no enforcement authority; the selected offer goes through the unchanged
  deterministic gate.
- **Tests:** S4/K1 now exercises the hostile-ontology boundary without network
  or an LLM key; full invariant and pytest results are recorded in the IaC
  release entry.
- **Decision:** none; this restores the existing proposal contract and S4.
- **Contracts touched:** none.

---

## 2026-08-30 — every mandate a transaction was transacted against

- **Why:** asked for a view showing, per transaction, every mandate involved.
  The honest answer is a list rather than one row: a sticky approval issues a
  one-shot child mandate carrying `parent_jti`, and `reserve_chain`/`settle`/
  `release` walk the whole ancestry (H6/K1), so a purchase generally debits more
  than one mandate. Showing only the mandate named on the intent would hide the
  limit that actually constrained the purchase.
- **Built (additive only, no existing behaviour changed):**
  - `service.purchases()` — transactions newest first, each carrying
    `mandate_depth`, so a chain of more than one is visible in the list.
  - `service.purchase_trace()` — the ancestry child first, each entry with the
    limits in force, what this purchase debited from it, and the resulting
    `spent_total`/`txn_count`; plus the signed intent and the chain events. It
    reconstructs from the rows the gate wrote and never recomputes a decision —
    a verdict recomputed later is not evidence of what was decided then.
  - `GET /agent/purchases` and `GET /agent/purchases/{id}/trace` on the bridge.
  - `web/` Transactions view: the ancestry as a ladder, and an amber callout
    where a mandate's `debited` exceeds its own `max_per_txn` — the ancestor
    case, which is the guarantee working rather than an error.
- **Tests:** unchanged from baseline — agent 42/42, Python 380 passed / 1 failed
  / 2 skipped (the pre-existing hardcoded-DSN test), web build clean. Endpoints
  exercised over HTTP: 200 on both, 404 on an unknown purchase.
- **Found and fixed:** the first cut matched chain events on `purchase_id` only,
  which silently dropped the gate's own verdict — `purchase.gated`,
  `purchase.verified` and `purchase.refused` carry `intent_jti` instead. A
  rejected transaction now shows `requested -> gated REJECTED -> rejected
  BUDGET_EXCEEDED` rather than two bookend events.
- **Open questions:** `src/api/deps.py` still wires the decision and ledger
  services to in-memory stores, so `/audit/events`, `/purchases/{id}` and the
  evidence pack read an empty world rather than Postgres. This view routes
  around that through the agent bridge; wiring `deps.py` to the Postgres
  repositories is the real fix and is untouched here.

---

## 2026-08-30 — Partial demo seed no longer leaves the dispatcher without authority

- **Why:** a local volume could retain `flights_marta`/`rappi_comprador` while
  losing every `mandates` row. The dispatcher correctly found no eligible
  pair, but the frontend only surfaced its generic 404.
- **Built:** `needs_demo_seed()` distinguishes a fresh or recognisably partial
  demo dataset from real mandate state. It restores the standard seed only in
  the former case; any existing mandate row, including revoked, suspended, or
  expired, remains fail-closed and is never replaced. Candidate lookup now
  precedes the optional LLM classification, so missing authority fails quickly
  instead of consuming a model timeout.
- **Tests:** empty and partial demo datasets recover; a custom agent without a
  mandate and a revoked mandate do not trigger reseeding; no-candidate routing
  proves the LLM is not called.

## 2026-08-30 — Telegram talks to the same /agent conversation

- **Why:** the buyer console already hits `/agent/ask` and `/agent/dispatch`;
  judges operate from their phones (decision #13). The webhook in the contract
  was an empty path.
- **Decision:** none needed (#13 already chose Telegram). Webhook at
  `POST /bot/telegram` (always 200, secret-token fail-closed). Local demo
  long-polls (`TELEGRAM_MODE=polling`) so we do not need a public HTTPS URL.
  One Telegram chat = one `session_id` (`tg:<chat_id>`); a live run stays on
  that agent, a new request goes through the dispatcher. Approve/Reject are
  inline buttons; the pending message mutates to APROBADO/RECHAZADO.
- **Built:**
  - `src/agent/telegram.py`: parse Update, `service.turn`, inline keyboard, Bot API via httpx.
  - `src/api/routers/bot.py` mounted on the kernel; polling in kernel lifespan.
  - `src/agent/service.turn` + `session_binding` so `/agent/dispatch` and Telegram share the continue-or-route rule.
  - CLI: `uv run python -m src.agent.cli telegram`.
- **Tests:** `tests/test_telegram_bot.py` — parse, session bind, secret fail-closed, webhook always 200, buttons on escalation.
- **Open questions:** `/revoke` from Telegram still refuses (passkey is the
  revocation ceremony). Binding a Telegram `user_id` to a passkey identity
  is not in this cut.

---



## 2026-08-30 — one schema, and the console stops inventing evidence

- **Why:** the database had four descriptions that disagreed, and the console
  fell back to a 990-line mock engine on any backend failure — including on the
  two calls that render the audit chain. Decision
  [#29](../decisions/0029-one-schema-source-of-truth.md).
- **Built:**
  - `aval/contracts/fixtures/schema.sql` is now the only schema. `src/agent/db.py`
    reads it instead of inlining a copy; Alembic applies it; `src/api/db/schema.sql`
    is a pointer. The six fraud tables it defined — `velocity_counters`,
    `risk_lists`, `risk_subjects`, `baseline_metrics`, `baseline_hists`,
    `webhook_archive` — are now actually created; the composed stack had none of
    them while `decision/repository_postgres.py` wrote to them.
  - api and merchant adapted to the agent's shape for shared tables: `jti`-keyed
    mandates, `mandate_jti` on children, TEXT money. `src/merchant/catalog.py`
    sorts price with an explicit `CAST(... AS NUMERIC)`.
  - `"window"` quoted in all 8 SQL sites (a Postgres keyword — every velocity
    write raised a syntax error), and the idempotency `response` dict now
    round-trips through `json.dumps`/`loads` instead of failing to adapt.
- **Tests:** agent 42/42; Python suite 376 passed / 1 failed / 2 skipped against
  a database built from the canonical file (was 373/4). The remaining failure is
  the hardcoded-DSN test in `test_webhooks_and_mcp.py`, untouched.
  `test_concurrent_relays_do_not_duplicate_skip_locked` flakes at file scope
  (~1 in 5) on `21 == 20` — a second drain of one event, which is the
  at-least-once delivery the relay documents, asserted as exactly-once.
- **Found and fixed in review:** the first cut of the E1 trigger listed nine
  protected columns, which left `actor`, `agent_id`, `run_id` and `mandate_jti`
  mutable on a committed audit row. They are inside the hash, so a replay would
  have caught it — but E1 says the database refuses, not that the auditor
  notices later. The trigger now compares whole rows and `root_sig` is
  write-once. Also: `TABLES` still listed only the agent's 20, so `cli reset`
  quietly left 14 identity, rail and fraud tables full of yesterday's rows.
- **Open questions:** the two chains still have two hash algorithms in one
  partitioned table (F5). The buyer purchase flow in `web/` never calls the
  backend, so the evidence viewer honestly reports "no pack" for it.

---

## 2026-08-30 — `offers.active` broke seed and 38 property checks

- **Why:** `main` could not run `cli seed` from a clean clone: the catalog insert
  crashed with `column "active" is of type integer but expression is of type
  boolean`. The property suite was at 4/42 on *any* database, private or shared —
  so this was never the shared-schema collision it looked like.
- **Built:** one word. `db.py` declared `active INTEGER NOT NULL DEFAULT 1` while
  `mocks/merchant.py` had moved to boolean semantics (`active=TRUE`,
  `WHERE active IS TRUE`). The DDL now says `BOOLEAN NOT NULL DEFAULT TRUE`.
- **Tests:** 42/42 green again; `reset` → `seed` → `demo` runs end to end.
- **Found:** the agent lane contradicted *itself*, and Alembic 0001 applies
  `db.SCHEMA` verbatim, so the migration carried the same mismatch. The cheapest
  possible instance of the four-schema problem, and the argument for fixing the
  root cause rather than the symptom.

---

## 2026-08-30 — Frontend-to-Backend Agent Bridge, PostgreSQL schema unification & Gemini live integration

- **Why:** wired frontend chat directly to real `/api/agent/ask` backed by Google Gemini and unified PostgreSQL database schemas.
- **Built:**
  - `web/src/components/BuyerConsole/AgentChat.tsx`: calls real backend agent endpoint and displays live Gemini model responses and security injection alerts.
  - `web/src/services/api.ts`: enabled real backend mode by default and added `askAgent` client method.
  - `src/agent/db.py`: DSN fallback handling SQLAlchemy prefixes and container host resolution.
  - `src/agent/graph.py` & `kernel.py`: safe JSON parsing helper handling both raw dicts and strings from PostgreSQL.
  - `src/agent/service.py`: flexible agent_id and mandate_jti resolution.
- **Tests:** 310 passing tests across test suite.

---

## 2026-08-30 — Google Gemini LLM integration, .env configuration and container propagation

- **Why:** user requested connecting live agent proposals to real Google Gemini models via API key without fallback mocks.
- **Built:**
  - `src/agent/config.py`: auto-detection for `GEMINI_API_KEY`, `GOOGLE_API_KEY`, defaulting to Google Generative AI endpoint (`https://generativelanguage.googleapis.com/v1beta/openai`) and `gemini-1.5-flash`.
  - `src/agent/net.py`: updated default allowed egress hosts to include `generativelanguage.googleapis.com`.
  - `.env` & `.env.example`: template for user API key injection.
  - `compose.yaml` & `docker-compose.yml`: mounted `.env` and forwarded LLM variables to container services.
- **Tests:** 310 passing tests across suite.

---

## 2026-08-30 — ports, live merchants, console auth, egress

- **Why:** everything on the open list that did not need another lane.
- **Decision:** [#28](../decisions/0028-postgres-ports-and-console-auth.md).
- **Built:**
  - `ports/` — protocols and registries for merchants, rails, models, channels.
    `ToolRegistry` refuses any effect but `read`/`submit` at construction, so S2
    is one assertion. Adapters for the real `vuela-ya` and `mami` MCP servers,
    plus the in-process mock behind the same interface.
  - `auth.py` — bearer tokens hashed at rest, four roles, enforced at the CLI
    and `service.py` edges. The console recorded who *claimed* to make a change;
    it now records an authenticated principal. Closes P7.
  - `net.py` — egress allowlist checked before any outbound call. Closes the
    in-process half of P6; VPC rules are still the production answer.
  - `service.py` — one module for `src/api/` to import: `ask`,
    `resolve_escalation`, `tick`, `publish_ontology`, `trail`, `verify`.
- **Empirical:** bought a real flight (VY-5F24E1, 165000 COP) and a grocery
  order through the live MCP servers, both through the gate, both refused after
  revocation. Both servers expose a `pay` tool that settles with no mandate;
  ours records it as refused and never calls it.
- **Found and fixed:**
  1. `submit_purchase` had callers passing no merchant (the watcher, escalation
     retries) and the registry was empty unless `setup()` ran. Merchant id now
     travels in the intent and the audit event.
  2. `memory.purchase_history()` joined the local `offers` table, so purchases
     made through a merchant's MCP were missing from the buyer's own history.
     It reads receipts now. Test A5.
  3. `auth` modelled roles without agent attachment, so an owner who was also
     the named approver could not resolve their own escalation. The role grants
     the capability, the attachment grants the instance; both are checked.
- **Contracts touched:** `people.token_hash` (migration 0004).
- **Tests:** X1–X5, A1–A5, G9 added. 42/42, including X4 which runs against the
  live merchant MCP and skips cleanly when it is not up.
- **Open questions:** merchant-side `pay` is still ungated — their repo, their
  call, written up in `../MCP-HANDOFF.md`. Bearer tokens have no rotation.

---

## 2026-08-29 — guardrails, quotas and a sandbox (`limits.py`, `deploy/`)

- **Why:** asked what stops a malicious prompt polling the merchant every
  0.01 s. Checked first: the model has no tool that sets an interval, creates a
  watch or spawns a run, so the instruction is unrepresentable. Built the other
  three layers anyway.
- **Decision:** [#27](../decisions/0027-guardrails-and-containment.md).
- **Built:** `src/agent/limits.py` — persisted token buckets, windowed counters,
  single-flight locks, and guards for watch interval/count, merchant and LLM
  call rates, runs per hour, steps, wall clock, prompt size, escalations per
  approver. `deploy/sandbox.sh` (bubblewrap), hardened
  `trytrust-watch.service`, `deploy/SECURITY.md`, `cli limits`.
- **Contracts touched:** `schemas.md` §6 additionally needs `rate_buckets`,
  `locks`, `counters` (additive; folded into the same pending PR as #20).
- **Tests:** G1–G8 added, 28/28 green. G9/G10 (sandbox containment) measured by
  hand and recorded in SECURITY.md — `~/.ssh` blocked, `.env` blocked, writes to
  `$HOME` land on tmpfs, and `kernel.py` is read-only to the sandboxed process.
- **Found and fixed:** `max_seconds or QUOTA.max_run_seconds` treated an
  explicit `0` as unset, so a caller asking for no time budget got the 120 s
  default — a guardrail failing open. Same bug in `clamp_text`. Both now
  `is None`. Caught by test G6, which is the argument for writing the test
  before believing the code.
- **Open questions:**
  1. **No auth on the console** (P7) — anyone who can run the CLI can publish an
     ontology. Biggest hole now. Needs Dev 3/4 input on where auth lands.
  2. Egress allowlist (P6) is named in SECURITY.md, not built.
  3. Single-flight uses SQLite; multi-instance Cloud Run needs Postgres
     advisory locks. Dev 4.

---

## 2026-08-29 — vertical slice running end to end (`src/agent/`)

- **Why:** M0 was blocked on other lanes, so Dev 1 built against local mocks
  instead of waiting. The whole flow now runs from a clean clone with no
  external service: `reset` → `seed` → `demo`.
- **Decision:** [#26](../decisions/0026-agent-memory-ontology-and-console.md) —
  memory + ontology + configuration console. Ontology is unsigned advice,
  the mandate is signed law; neither memory nor ontology reaches the gate (S4/K1).
- **Built:**
  - `graph.py` — the own-graph orchestrator (#16): `perceive → search → propose
    → gate → await_human → receipt`, checkpointed in `agent_runs`, resumed by
    escalation resolution. Replan is keyed on `ReasonCode`: only
    `AMOUNT_MISMATCH` and `RAIL_ERROR` retry; everything else stops, because
    retrying a revoked mandate fills the log with noise.
  - `chat.py` — a person asks, interrupts, approves or redirects mid-run.
    Guidance while parked rejects the pending escalation and replans.
  - `watcher.py` — standing watches with human-set JsonLogic thresholds; a tick
    also expires escalations, which is what makes the 120 s fail-closed timeout
    real when nobody is at a terminal. cron / systemd / Cloud Scheduler in `deploy/`.
  - `registry.py` — people, agents, versioned ontologies. Publish = 3 writes,
    1 transaction. `agent_runs.agent_version` is pinned at run start.
  - `crypto/` — JCS canonicalisation, Ed25519, compact + detached JWS. Real.
  - `llm.py` — `gpt-4.1-nano` via stdlib urllib, untrusted text fenced, and a
    deterministic fallback so no network failure can take the demo down.
  - Mocks for the other lanes: `mocks/merchant.py` (catalog, 3 MCP tools,
    checkout that verifies the mandate itself), `mocks/rail.py` (vaulted token,
    idempotent capture, DELETE kill switch), `kernel.py` (the real decision table).
- **Contracts touched:** `schemas.md` §6 needs `people`, `agents`,
  `agent_versions`, `watches`, `chat_messages`, and `agent_runs.agent_version`
  — PR pending, decision record already written (docs-guard #17).
- **Tests:** `uv run python -m src.agent.tests` — 20 checks, each named after the
  property it defends ([`../PROPERTIES.md`](../PROPERTIES.md)). All green.
  Covers T1 (determinism), T3 (impersonation), T7 (resume idempotency),
  T9 (chain), T10 (JsonLogic), T11 (injection), T12 (scrubber).
- **Found and fixed:** a sticky approval issued a mini-mandate with its own
  budget, which quietly minted spending power. Reservation, settlement and
  release now walk the whole parent chain — a child can never spend what its
  parent cannot (K1/H6).
- **Open questions:**
  1. `audit_events` columns must match Dev 2's migration or we get two chains.
     Needs five minutes with Dev 2 before either migration lands.
  2. Product renamed to **TryTrust** (`trytrust.lat`). The domain does not
     resolve yet and passkeys need a real registrable domain (P2, decision #3).

---

## 2026-08-29 — workstream opened at M0 freeze
- **Why:** contracts v1.0 are frozen; this log exists so nobody re-solves what Dev 1 already solved.
- **Decision:** this workstream starts from
  [`../decisions/0016-agent-orchestration-without-langgraph.md`](../decisions/0016-agent-orchestration-without-langgraph.md):
  own ~150-line graph — LLM only in `propose`, checkpointing in `agent_runs`,
  `await_human` resumed by escalation resolution. **No agent-framework
  dependency.** Identity keys: [`../../DECISIONS.md`](../../DECISIONS.md) #9.
- **Contracts touched:** `schemas.md` §5 (escalation resume), §6 (`agent_runs`
  DDL — my migration), §10 (MCP tools I consume from Dev 3).
- **Tests I own:** T3 (impersonation), T7 (resume idempotency, with Dev 2),
  T11 (injection suite), T12 (Presidio).
- **Open questions:** none.
