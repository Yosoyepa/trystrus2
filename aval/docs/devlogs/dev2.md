# Devlog — Dev 2 · Fraud, contracts, idempotency (kernel: decision core)

Mission: nothing out-of-mandate ever passes; the signed contracts verify for
real; every operation is exactly-once with cryptographically verifiable
evidence. Scope and day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md)
§3. Entry protocol: [`README.md`](README.md) — newest first, every PR.

---

## 2026-08-30 — The chat can reach the real local Rappi bridge from compose

- **Incident:** `/agent/dispatch` selected `rappi_comprador` and returned 200,
  but the run found zero offers. Direct host search returned 286 live Rappi
  products; from `aval-kernel`, the configured `169.254.1.2:8010` refused the
  connection because the credential bridge correctly bound host loopback.
- **Built:** `rappi_bridge` is now a compose service on the private
  `aval-network`, with only `127.0.0.1:8010` published for the configuration
  console. The kernel uses `http://rappi_bridge:8010`, waits for its health,
  and Rappi registration itself checks `/healthz` instead of claiming an
  untested connection. The local launcher also starts the bridge explicitly.
  `DRY_RUN=true` and the hard COP cap remain the defaults; the session file
  stays a chmod-600 bind mount on the credential machine. In compose, the
  bridge receives only its explicit `AVAL_BRIDGE_*` settings; it does not
  inherit database or model credentials from the repository `.env`. The local
  launcher waits for bridge health before starting the kernel, so registration
  cannot lose a startup race.
- **Chat correctness:** explicit search language (`busca`, `muéstrame`,
  `opciones`) now stops after the real read-only proposal; purchase language
  still wins when both appear. Finished runs are returned to the UI with the
  merchant title, price and CDN images instead of disappearing as `run:null`.
  A Rappi-only search sends the original query directly to its catalogue and
  ranks the returned offers deterministically. Obvious grocery keywords also
  route without a model, removing the irrelevant model waits that could exceed
  the web BFF's 25-second timeout.
  Rappi cart hashes also normalise equivalent integer/float/string numbers,
  fixing the live quote's JCS rejection without introducing float arithmetic.
- **Verification:** compose topology tests cover both compose files. Runtime
  verification exercises bridge health from the kernel, live catalogue search,
  and the chat dispatcher without a paying click. The cluster smoke test now
  covers bridge health and the web-to-agent mandate route alongside the
  existing kernel, Yuno and merchant routes.

## 2026-08-30 — Schema alignment corrected: fresh DB follows schema.sql (agent shape), not the old alembic shapes
- **What broke live:** after the volume wipe, kernel and merchant went into
  crash loops. Root cause of the confusion: TWO different database shapes
  existed tonight. The pre-wipe DB was created with alembic-era API shapes
  (`escalations` timestamptz, `offers` numeric/travel_date) — tonight's
  fixes targeted THAT db. The post-wipe DB loads the current
  `schema.sql` (decision 0029 agent shape): `escalations` TEXT with NO
  `resolved_at` column, `offers` TEXT with `depart_date`. My earlier
  "fixes" (DateTime binds, numeric mapping) were correct for a database
  that no longer exists and crash-looped the fresh one.
- **Alignment (git-revert to 4fed684 for the four files, then adapt):**
  merchant OfferRow back to TEXT/depart_date; escalations service back to
  ISO-string binds; the ONE real schema gap patched properly — the service
  no longer binds/reads `resolved_at` (column does not exist in schema.sql;
  the view uses created_at). Suite 319 green.
- **Live-incident note:** the user-visible 404 on /agent/dispatch was the
  kernel crash-looping during the user's request; the front fell back to
  /agent/ask with a stale mandate id (env updated to mdt_0492… now).
- **Router:** added beverage keywords (botella/agua/gaseosa/jugo/leche/pan)
  so drink requests route to the Rappi agent even without an LLM category.

---

## 2026-08-30 — Payments integrated from the fork (6a20e1e): CASH fallback exposed and killed, 3DS reality mapped
- **What the fork taught us (DaniDiazTech/rappi-cli @ 6a20e1e, fetched into
  the vendor clone and audited):** without a payment method on the cart,
  Rappi charges the order **IN CASH, silently** — the CLI never called
  `setPaymentMethod`, so the live Claude order was a CASH order (corrects
  our earlier doc). Payment methods live in a separate resolver
  (`GET /api/ms/payment-method/resolver/v6`), the cart PUT
  (`.../payment-method`) needs the web payload with empty values dropped,
  and cards tagged `require_3ds_by_fraud` cannot be charged from automation
  (3DS challenge is app/browser-only).
- **Bridge integration:** `get_payment_methods` + `set_payment_method` +
  `build_payment_payload`/`method_is_cash`/`method_needs_3ds` ported; the
  guarded flow now resolves → applies → RE-QUOTES (applying a method can
  move totals → PriceDrift) → guards → armed. Policy, fail-closed: preferred
  resolver id from config must exist (never silently charge another
  instrument); cash refused unless `AVAL_BRIDGE_ALLOW_CASH=1`; a
  3DS-flagged card raises `BRIDGE_CARD_3DS_REQUIRED` instead of creating an
  order the antifraud will cancel. `GET /v1/rappi/payment/methods` exposes
  the list (+selected/cash/three_ds flags) for the front.
- **Live account reality:** the connected session's resolver shows both
  cards fraud-flagged (`require_3ds_by_fraud`) — card purchases through the
  bridge will fail-closed until Rappi clears the flag (repeated test orders
  appear to trigger it; the fork saw the same). Demo implication: use the
  wait-and-clear path, another account, or finish 3DS orders in the app.
- **Tests:** +9 payment tests (payload mirror, cash detection, apply-before-
  click, cash refused, missing preferred fail-closed, explicit cash opt-in,
  receipt carries method, 3DS refused, 3DS helper) — bridge suite 50 green,
  repo 319 passed. LazyRappiClient passthrough fixed (private-name guard
  was hiding `_request` → 500 on /payment/methods without session).

---

## 2026-08-30 — Agent dispatcher LIVE: request → right agent → right MCP; Rappi registered as a merchant
- **What shipped:** `src/agent/router.py` — request-to-agent routing.
  Category from `llm.parse_request` (propose-side, 0016-safe) + a
  deterministic keyword/scope scorer over every active (agent, mandate) pair;
  the reply explains WHY it routed ("categoría 'flights' en el scope…
  palabras ['vuelo']…"). Ambiguity with >1 zero-score candidates refuses to
  guess. New `POST /agent/dispatch {text}` runs the routed agent and returns
  the routing decision next to the replies. 4 pure scoring tests.
- **Rappi is now a first-class merchant:** `RappiBridgeMcp` (merchant_id
  `rappi`) registered via `ports/setup.py` (`TT_RAPPI_BRIDGE_URL`) — search
  fans out to the bridge's real session through the new read-only
  `GET /v1/rappi/search` (audited CLI endpoint); settle is DELIBERATELY
  refused (`CAPTURE_PENDING`) because a Rappi charge is the bridge's guarded
  click with a kernel capture token, never a merchant-side charge (0030).
  Kernel lifespan now calls `agent_service.bootstrap()` so real MCPs register
  at boot (`merchants registered: ['rappi', 'vuelaya']` in the logs) — until
  today only the LocalMerchant mock was ever registered, which is what the
  earlier "Bought" demo actually hit.
- **Seeded:** `scripts/seed-rappi-agent.py` → agent `rappi_comprador`
  (agt_7d4c…) with a dual-signed COP mandate (60k/txn, 200k total, scope
  rappi/food/groceries/retail). Flights mandate re-issued for agt_c124
  (mdt_0492…). NOTE: one flights mandate pair vanished from the DB between
  the reset and the rebuild with no DELETE statement in the codebase —
  re-issued and stable since; watch if it recurs.
- **E2E verified:** "Buscame un vuelo de BOG a COR" → flights_marta →
  Bought 130.00 USD (receipt rcp_7797…, Gemini rationale in Spanish);
  "papas pringles en rappi" → rappi_comprador → routed correctly, search
  refused safely while no Rappi session exists (fail-closed as designed).
- **Front:** chat calls `/agent/dispatch` and prefixes replies with the
  chosen agent; pinned-agent envs remain as fallback only.

---

## 2026-08-30 — Backend LIVE via podman (user's runtime): three integration bugs found and fixed
- **Setup:** stack brought up with `podman compose up -d --build db kernel
  yuno_sim merchant` (no Docker on this machine; Podman 5.8.4 is the team
  runtime). DB seeds `schema.sql` on first init; agent seed auto-runs on
  first `/agent/*` call (agents `agt_b8b3…`, mandates `mdt_28de…` USD/
  vuelaya and `mdt_c672…` COP/vuelaya-mcp+mami).
- **Bug 1 (mine):** yesterday's playwright optional-extra patch placed
  `[project.optional-dependencies]` BETWEEN `requires-python` and
  `dependencies`, kicking the runtime deps out of `[project]` — `uv sync
  --no-dev` in the Dockerfile installed an EMPTY venv (`uvicorn not found`
  at container start). pyproject reordered; lock regenerated; lesson: TOML
  table headers split tables.
- **Bug 2 (schema drift, merchant):** `OfferRow` mapped `depart_date` TEXT +
  `amount` TEXT but the unified fixture schema has `travel_date DATE` +
  `amount NUMERIC(12,2)` — merchant died at startup seeding. Fixed by
  mapping to the real columns and converting at the `to_offer` seam
  (DTO stays TEXT money). Merchant healthy, 13 offers seeded.
- **Bug 3 (schema drift, api services):** the escalation sweeper bound ISO
  STRINGS into `timestamptz` columns — `operator does not exist: timestamptz
  < character varying`, 72 errors; same drift class. `models.Escalation`
  timestamp columns are now `DateTime(timezone=True)` and
  `services/escalations.py` binds datetimes (`_utc`); `_from_iso` tolerates
  legacy TEXT rows. This unblocks the step-up path of `/agent/ask`.
- **Known data inconsistency (team decision needed):** the COP mandate scope
  references merchants `["vuelaya-mcp","mami"]` but every catalog row is
  `merchant_id='vuelaya'` (which only the USD mandate allows) — the COP agent
  can never find a purchasable offer. Mandates are signed (not editable);
  either fixtures move to `vuelaya-mcp` or a `mami` catalog is seeded.
- Front now wires to the live kernel via `trytrust-platform/.env.local`
  (KERNEL_URL/AGENT_ID/MANDATE_JTI/BRIDGE_URL).

---

## 2026-08-30 — OTP login blocked by Rappi antifraud (400 «looks_bad») → real fingerprint + persistent profile + manual-token plan B
- **What happened:** first real attempt at Config Rappi reached the OTP screen
  but `POST /api/rocket/login/whatsapp/application_user` answered 400
  `looks_bad` — the antifraud rejected the exchange. Root cause (high
  confidence): our context spoofed a mobile UA over real desktop Chrome, so
  client hints (`sec-ch-ua-platform`) contradicted the claimed device; plus a
  brand-new profile = zero device trust. Yesterday's successful login ran on
  the owner's real Chrome fingerprint.
- **Fixes:** `_launch_browser` no longer spoofs UA and uses a PERSISTENT
  profile (`AVAL_BRIDGE_LOGIN_PROFILE_DIR`, var/rappi-bridge/login-profile —
  dedicated dir; the owner's personal Chrome profile is untouched) so the
  device fingerprint is stable and accumulates trust. Plan B that cannot be
  blocked: `POST /v1/rappi/session/manual {token}` (DevTools →
  services.grability… request → Authorization header) —
  `LoginFlow.connect_with_token` validates the `ft.` prefix, probes whoami,
  writes the 0600 session file; invalid input raises BridgeError (409).
  Front panel gained the manual-token section with step-by-step instructions.
- **Tests:** +4 (manual connect custody, non-ft rejection, HTTP endpoint,
  profile-dir contract) — bridge suite 41 green, ruff clean. Operator note:
  always request a FRESH WhatsApp OTP per attempt; stale codes also fail.

---

## 2026-08-30 — Config Rappi LIVE: OTP login capture in the bridge + Tower Control front wired
- **What shipped (bridge):** `login.py` — headful OTP login capture via the
  owner's own Chrome (`channel="chrome"`, chromium fallback, 5-min window);
  passive `ft.` token capture from `/ms/application-user/auth`; session file
  written chmod 600 with the active address's coords; status machine
  idle→waiting_login→captured|error with MASKED labels only (name initials +
  bullet email — the run real's whoami masking bug is now our code too).
  `LazyRappiClient` lets the bridge start with NO session and picks up the
  fresh token by file mtime. Endpoints: `GET/POST /v1/rappi/session/{status,
  login}`, `DELETE /v1/rappi/session`; CORS for the platform front
  (`AVAL_BRIDGE_CORS_ORIGINS`). playwright as optional extra
  (`uv sync --extra rappi`); lock updated. 7 new tests (custody 0600, single
  window on double start, error path, endpoints, CORS); suite 37 bridge /
  307 total green.
- **What shipped (front):** bysergr/trytrust-platform was an empty "Tower
  Control" placeholder — built the chat shell (messages, input, kernel
  `/agent/ask` wiring with graceful echo fallback) and the **Config Rappi**
  button + side panel: status dot (idle/waiting/captured/error), "Iniciar
  login OTP" with 3-step instructions, 2s polling while waiting, masked
  account + active address on success, disconnect, and a bridge-unreachable
  hint. Local bring-up verified: bridge :8010 (healthz dry_run=true, CORS
  header present), front :3000 serving both strings, playwright 1.62 +
  system Chrome detected.
- **Note:** an over-eager `ruff --fix` touched `src/api/routers/mandates.py`
  (team lane) — reverted; commit stays scoped. F0 of the demo can now start:
  click Config Rappi → OTP → status captured → preflight.

---

## 2026-08-30 — Rappi bridge BUILT (decision 0030): guard edge + capture tokens, 30 tests, suite 307 green
- **What shipped:** `src/rappi_bridge/` — the guarded execution edge running
  on the credential machine only. Native httpx port of the audited CLI
  endpoints (no Bun in prod); kill switch; DRY_RUN default-on; hardcoded COP
  cap; clean-cart precondition; delivery-address binding; cent-exact drift
  rejection; SQLite single-flight with checkpoint machine that NEVER
  re-clicks an `uncertain` order and treats Rappi's store-minimum rejection
  as retryable `failed`. Kernel side: `decision/capture_token.py` (mint,
  `typ=capture-token+jwt`, TTL 120 s, binds purchase+reservation+amount+
  cart_hash+dry_run) and the additive `MerchantBridge` port.
  Contract `aval/contracts/rappi-bridge.yaml` (api.yaml untouched);
  DECISIONS #30; full record `docs/decisions/0030-…`.
- **Tests:** 30 new (token roundtrip + 8 rejections incl. expired/tampered/
  wrong-key/dry-run-mismatch; guards; single-flight + ARMED→CLICKED race;
  dry-run never clicks; live clicks once; replay returns the original
  receipt; uncertain refuses the retry; min-amount retryable; kill switch).
  Full suite: 307 passed, 68 db-skipped, ruff clean. One real bug found by
  the state tests: claim() self-deadlocked a non-reentrant Lock — now RLock.
- **Integration/prod plan:** `plans/2026-08-30-dev2-rappi-integration-prod-plan.md`
  — flow diagram, topologies (A local demo / B Cloud Run + outbound tunnel /
  C pull-worker), env vars, runbook, and the remaining Dev 3 brief (mint on
  pending_capture, `POST /purchases/{id}/capture`, outbox events,
  bridge_artifacts in the evidence pack). Estimate 1–1.5 days pairing.

---

## 2026-08-30 — First LIVE run analyzed: real order placed via CLI; HITL pattern mapped to Aval; 4 new guardrails
- **What happened:** a Claude Code session drove `@crafter/rappi-cli`
  end-to-end on the owner's machine and placed a REAL paid order
  (`2496728264`, $18.300 COP, Turbo Parque Bavaria) after a conversational
  confirm. Factibility is no longer open: login (token capture via the
  owner's own Chrome, no CVC needed at checkout), search, cart, checkout and
  place-order all validated in production.
- **Measured failure modes (validate the design):** price drift search
  $9.400 → checkout $10.300 with a store re-resolution AND a service fee
  invisible in search; split-brain addresses (search uses local coords,
  cart/checkout use the account's active address); server-side store minimum
  rejected the first order with no money moved; `remove-from-cart` is broken
  (DELETE 404) and the PUT cart call REPLACES store contents.
- **New guardrails added to the blocking checklist:** (11) address binding —
  bridge asserts checkout delivery address against the mandate; (12) clean
  cart precondition + PUT-replace semantics; (13) structured replan for
  `MERCHANT_MIN_AMOUNT`; (14) mandate ceilings and step-up ratios computed on
  the checkout TOTAL, never on search prices.
- **HITL mapping:** the session's two-phase UX (preview → explicit confirm →
  execute) is exactly our escalation shape but without enforcement; in Aval
  the "sí" becomes a passkey-signed escalation resolution, the execution is
  gated by capture_token bound to amount+cart_hash with a pre-POST re-fetch
  (drift aborts), and a repeated confirm cannot duplicate an order
  (single-flight). What we keep from the session: full transparency before
  confirm and the "I place nothing until you confirm" stance.
- **Artifacts:**
  [`research/2026-08-30-dev2-rappi-live-run-findings.md`](../research/2026-08-30-dev2-rappi-live-run-findings.md).
  Work continues on `main` per team direction; F0′ is effectively done, next
  is the guard bridge + F1 dry-runs on the secondary account.

---

## 2026-08-30 — `@crafter/rappi-cli` audited: adoption approved as bridge substrate (not as agent surface)
- **Why:** teammate shared an npm CLI that orders from Rappi directly via the
  web-internal API (`services.grability.rappi.com`) with CLI + REST + MCP
  surfaces. Cloned to `vendor/crafter-station-rappi-cli` (pinned `5c4e5b0`)
  and audited WITHOUT installing; tarball verified identical to the clone.
- **Security audit: clean.** Only rappi/grability hosts (+ npmjs version
  check), no eval/child_process/postinstall, mainstream deps only, token
  gitignored by the author. Tiny adoption (11 dl/week, 34 stars) ⇒ pin the
  commit, re-audit on any update.
- **Key mechanics learned:** login captures the web session token
  (`Bearer ft.…` Fernet) passively from `/ms/application-user/auth` while the
  owner does their normal OTP login (browser ONLY for login); place order =
  POST `shopping-cart-proxy/{storeType}/checkout` with the server-issued
  `return_key` (cousin of our capture_token) and charges the ALREADY-selected
  saved card — no CVV in flow; `checkout/detail` gives authoritative totals
  for a trivially deterministic drift check.
- **Verdict:** do NOT expose its MCP to the agent (raw `place_order` tool =
  unguarded money path, violates S2). DO build `aval-rappi-bridge` on top of
  it (Option A: run CLI's REST on localhost:3100 behind our Python guard
  layer with DRY_RUN/cap/single-flight/capture_token; Option B later: port
  the ~10 service files to httpx). API path replaces Playwright/DOM as
  primary; DOM bridge demoted to plan C if the undocumented API rotates the
  `app-version` hash. Kernel-gated architecture and decision #29 unchanged.
- **Artifacts:**
  [`research/2026-08-30-dev2-rappi-cli-evaluation.md`](../research/2026-08-30-dev2-rappi-cli-evaluation.md)
  (+ update banner on the bridge analysis). Next: F0′ smoke test on secondary
  account (login → search → cart → checkout_preview, NO place order).

---

## 2026-08-30 — Rappi bridge use case evaluated (4 expert agents): merchant-with-embedded-charge, not a rail
- **Why:** happy-path merchant (Rappi) has no buyer-side API; proposal is to
  operate a real logged-in session (card + address vaulted in Rappi) from the
  credential machine so the agent can browse, search under the owner's
  identity, and place REAL orders end-to-end.
- **Verdict:** viable for the hackathon demo with hard guardrails. Key
  structural finding: the bridge is NOT a Yuno-style rail — Rappi vaults the
  card, so the final click IS the capture. Correct pattern is
  ChargeService/settle (kernel-only) + a new kernel-minted capture_token
  (JWT ES256, TTL 120 s, binding purchase_id+amount+cart_hash), NOT
  AsyncPaymentRail (frozen #24). Closes the saga's missing half
  (pending_capture → captured).
- **Choice:** own domain bridge (Playwright headful + persistent profile,
  6 closed actions, place_order gate-bound, screenshots hashed into the
  evidence pack), DRY_RUN default-on, 10-item blocking guardrail checklist
  (storage_state custody, hardcoded COP cap, no direction/card/coupon verbs,
  CVC fail-closed, never re-click on uncertain). Fallback kept warm:
  @playwright/mcp. Discarded: Android automation, private-API replay.
- **Empirical:** rappi.com.co loads with no visible anti-bot; web checkout
  works without the app; dev portal is B2B-only; ToS §10 silent
  fraud-check cancellation is the top demo-kill risk (plan B: simulator as
  primary demo).
- **Artifacts:** analysis + guardrails + build plan + open decisions
  Q-R1..Q-R8 in
  [`research/2026-08-30-dev2-rappi-bridge-analysis.md`](../research/2026-08-30-dev2-rappi-bridge-analysis.md);
  decision record #29 proposed (bridge as merchant-rail); contract additions
  live in a NEW `contracts/rappi-bridge.yaml` (api.yaml stays frozen).

---

## 2026-08-30 — Unified HTTP routers mounted in `src/api/` (Decision, Audit, Evidence, Agent Bridge)
- **Why:** Connect all underlying subsystems (policy gate, idempotency, atomic reservation, audit hash chain, canonical evidence pack, agent orchestrator) to the HTTP surface for the web frontend and external callers.
- **Implemented:**
  - `src/api/routers/decision.py`: `POST /mandates/{mandate_id}/verify` and `POST /purchases/verify` connected to `DecisionService`.
  - `src/api/routers/audit.py`: `GET /audit/events`, `POST /audit/verify`, `GET /audit/verify`, and `POST /audit/tamper`.
  - `src/api/routers/evidence.py`: `GET /purchases/{purchase_id}/evidence-pack` connected to `EvidenceService.assemble()`.
  - `src/api/routers/agent_bridge.py`: `POST /agent/ask`, `GET /agent/transcript`, `GET /agent/runs`, `GET /agent/watches`, `POST /agent/watches`, `GET /agent/limits`.
  - Mounted all routers in `src/api/main.py` and exported dependencies in `src/api/deps.py`.
- **Tests:** Added `tests/test_unified_routers.py` exercising 12 comprehensive cases with in-memory/in-process fakes. 100% of test suite passing (307 passed, 68 skipped). All ruff checks clean.

---

## 2026-08-29 — coder-1 run audited (5 agents): approved with observations; lanes merged (D2-I/I-1)
- **Why:** the decision-core run landed as C1–C5 (`d931ba9`..`9d18ce5`); the
  plan requires an audit gate before merging the decision lane with the
  evidence lane.
- **Done:** 5 parallel read-only audits — brief compliance, decision-core deep
  review, domain delta vs the fixes card, test-suite audit, merge readiness.
  Verdict: **approved with observations**. Lane discipline held (zero
  forbidden files, hypothesis contained to T1, devlog per commit, stash
  empty). Core wins: replay-verbatim idempotency with claim tokens, the
  double-escalation-on-replay bug is dead, G-2 closed at service level
  (catalog offer mandatory), release honors reservation_id, outbox shares the
  business transaction (rollback-tested), T5 race covered in the PG lane.
  Still open (fix cards 1/3/7 NOT executed, as expected for this run):
  `uv_verified` bypass live at domain level, `approved_stepup` is a public
  kwarg that converts ESCALATED→APPROVED with no trail annotation, offer
  currency unchecked (G-3: EUR offer approved on USD mandate), `fraud.alert`
  dead on the flood path (auto_suspend flag lost by the gate — D-01),
  DUPLICATE_JTI/purchase.requested still unemitted, `pending_capture` off-contract,
  no depth caps in the JsonLogic evaluator, secret default hardcoded. Then:
  merged `dev2/audit-evidence` into this branch (devlog resolved keep-both;
  pyproject auto-merged; uv.lock regenerated) per merge-readiness audit —
  conflict surface was exactly the predicted one.
- **Tests:** union suite green; ruff clean; docs-guard OK.
- **Decision:** none new (fix cards 1–7 remain the next run's contract).
- **Contracts touched:** none.

---

## 2026-08-29 — C1 implementation: velocity store foundation
- **Why:** R-BURST needs a deterministic counter projection that can be read
  before evaluation and updated atomically after an observed intent.
- **Done:** added the decision ports, lock-protected in-memory repositories,
  PostgreSQL minute-bucket upserts, cooldown expiry records, open-authorization
  counters, strict money handling, and transaction-aware outbox/reservation
  seams. The shared repository modules also contain the C2 idempotency adapter
  because both adapters use the same frozen persistence boundary.
- **Tests:** unit coverage exercises count and amount buckets, rolling-hour
  escalations, cooldown expiry, open-authorization compensation, derived-key
  enforcement, replay, conflict, retention, and purge behavior.
- **Decision:** no schema or contract files were changed; the frozen DDL
  remains authoritative.
- **Contracts touched:** none.

---

## 2026-08-29 — C2 implementation: idempotency claim ownership
- **Why:** a matching retry must replay the first response, while a
  concurrent request that sees an unresolved claim must not execute the
  purchase side effects a second time.
- **Done:** added the application claim coordinator. It derives the key from
  the intent JTI, creates a unique pending claim token, and exposes ownership
  explicitly to the verify use case. Repositories persist the token inside
  their namespaced JSONB metadata without changing the frozen DDL.
- **Tests:** added English unit coverage for first-owner and second-claim
  behavior while retaining the store tests for conflict, TTL, replay, and
  first-response preservation.
- **Decision:** non-owners fail closed until the owner has persisted the
  response; no payment or purchase side effect is repeated.
- **Contracts touched:** none.

---

## 2026-08-29 — C3 implementation: verify path and escalation saga
- **Why:** the deterministic policy result must become one durable business
  branch without allowing approval, infrastructure failure, or a stale offer
  to bypass enforcement.
- **Done:** wired the verify use case to mandate and offer ports, velocity
  observations, atomic conditional reservation, purchase state, escalation
  TTLs, lazy expiry, trusted UV validation, full re-gating, compensation, and
  transaction-aware outbox events. Repeated pending idempotency claims now
  fail closed before business side effects.
- **Tests:** added English fake-based coverage for approval, every principal
  rejection/escalation branch, burst cooldown, price TOCTOU, budget races,
  changed mandate state, trusted and missing UV, timeout compensation,
  outbox failure, catalog reload, and idempotent replay/conflict.
- **Decision:** the service never trusts a caller-supplied offer or UV flag
  when a repository/verifier is configured; the current DDL remains unchanged.
- **Contracts touched:** none.

---

## 2026-08-29 — C4 implementation: deterministic T1 properties
- **Why:** example tests protect known branches, but the gate invariants must
  also hold across generated amounts, scopes, statuses, counters, and rule
  boundaries.
- **Done:** added Hypothesis strategies and properties for out-of-mandate
  rejection, the verdictive/corroborative gold rule, inclusive step-up
  thresholds, burst cooldown transitions, HMAC key stability/injectivity,
  and L3/L3+ TTLs. The CI profile is registered with 200 deterministic
  examples and no deadline.
- **Tests:** the T1 property module runs in the normal test suite and uses
  exact Decimal values only.
- **Decision:** Hypothesis is the only new development dependency; runtime
  enforcement remains deterministic and model-free.
- **Contracts touched:** none.

---

## 2026-08-29 — C5 implementation: DB-marked integration coverage
- **Why:** the in-memory saga proves decisions locally, while the atomic
  PostgreSQL paths need executable checks when a development database is
  available.
- **Done:** added DB-marked checks for the frozen tables, velocity upserts,
  derived idempotency round trips, competing reservation connections, and
  transaction-bound outbox rollback. The module skips cleanly when
  DATABASE_URL or a PostgreSQL driver is absent.
- **Tests:** the default environment reports the DB module as skipped; a
  configured PostgreSQL run exercises the real conditional update and
  rollback behavior without mutating the schema.
- **Decision:** no test creates or alters DDL; fixture rows use unique IDs and
  are cleaned up transactionally.
- **Contracts touched:** none.

---

## 2026-08-29 — execution round 1: canonical unification (RT-9), evidence module (D-1), golden vectors, critical-fixes card
- **Why:** start resolving the gap register without colliding with the
  coder-1 run in flight — everything here is NEW files; the gate fixes that
  live in their WIP files are handed over as a precise fix card instead.
- **Done:**
  (1) `src/api/canonical.py` — the single strict canonical JSON (floats,
  sets, naive datetimes and non-str keys rejected; Decimal/datetime/Enum
  canonical), leaf module so domain/decision/audit/events can all adopt it
  after the lanes merge;
  (2) `src/api/tests/test_canonical_golden.py` — 12 tests incl. two golden
  vectors whose JSON literals are hand-written and whose SHA-256 digests were
  computed independently of the implementation (TX-10 groundwork);
  (3) `src/api/evidence/` — R-EVIDENCE pack use case (models/ports/service):
  assembles mandate + intent + decision + receipt + ledger slice + chain
  verdict + root checkpoint, fail-closed by construction (`integrity` ok /
  failed with explicit reasons, failures never hidden), digest over the
  canonical envelope; ports are lane-local (no imports from decision/ or
  audit/) so the composition root wires adapters after the merge;
  (4) `src/api/tests/test_evidence_pack.py` — 9 tests (happy path, digest
  stability, tampered chain, missing witness/receipt/slice/decision, unknown
  purchase, model invariants);
  (5) [`../plans/2026-08-29-dev2-critical-fixes-card.md`](../plans/2026-08-29-dev2-critical-fixes-card.md) —
  six code-level fix cards (RT-1 UV bypass, RT-2 replay wiring, RT-3/G-2/G-6
  fail-closed downgrades, G-5 silent-degraded modes, RT-6 step-up ratio in the
  reservation guard, minor debts) to run AFTER C1–C5, one commit per card.
- **Tests:** `uv run pytest src/api/tests/test_canonical_golden.py
  src/api/tests/test_evidence_pack.py` → 21 passed; ruff clean on the new
  files; coder suite observed in parallel: 62 passed / 1 skipped (their WIP,
  IndentationError already fixed on their side).
- **Decision:** none new.
- **Contracts touched:** none.

---

## 2026-08-29 — gate/gap analysis (8 parallel audits) + phase evolution plan
- **Why:** after the parallel-phase run was verified and merged, the next
  phase of Dev 2 needed ground truth: where the gate is incomplete, where the
  lanes have gaps, and what to build next.
- **Done:** ran 8 read-only audit agents over `dev2/gate-core` (+ coder WIP)
  and `dev2/audit-evidence` — gate completeness & fail-closed, coder-1 WIP vs
  brief, contracts↔code delta, threat/rule coverage + red-team, test matrix,
  integration seams, R-IDEM/disputes, persistence/ops. Synthesized:
  [`../research/2026-08-29-dev2-gate-gap-analysis.md`](../research/2026-08-29-dev2-gate-gap-analysis.md)
  (finding register RT/G/W/B/H/I/P/TX, threat & ladder coverage, seam map,
  open decisions Q-01..Q-10) and
  [`../plans/2026-08-29-dev2-phase-evolution.md`](../plans/2026-08-29-dev2-phase-evolution.md)
  (phases D2-C close-kernel → D2-I lanes-merge & seams → D2-S saga/recon →
  D2-D evidence/disputes → D2-B baselines, each with an exit gate, plus the
  cross-lane briefs Dev 3/1/4 need to unblock H-01..H-08).
- **Key findings (action required):** UV bypass via `uv_verified` (RT-1) and
  unwired replay protection (RT-2) must be closed before any HTTP wiring;
  silent-degradation fallbacks weaken R-PRICE (RT-3/G-2); offer currency
  unchecked (G-3); three divergent canonical JSONs (RT-9); decision hot-path
  state still volatile (no PG adapters for mandate/offer/purchase/escalation);
  P0 rules post-capture/webhook/evidence are orphaned across lanes with no
  active brief.
- **Snapshot caveat:** audits ran while the coder was live-editing
  (`service.py` grew 821→934 lines); re-verify findings when the run lands.
- **Decision:** none new (analysis only; Q-01..Q-10 listed for team).
- **Contracts touched:** none.

---

## 2026-08-29 — C1/C2 decision stores implemented
- **Why:** the deterministic gate needs atomic velocity observations and a
  replay-safe persistence boundary before the verify saga can safely write
  business state.
- **Done:** added the DEV2 decision ports, thread-safe in-memory fakes,
  PostgreSQL adapters for velocity_counters and idempotency_keys, strict
  cent-precision money validation, minute-bucket counters, cooldown and
  open-authorization tracking, HMAC-derived idempotency keys, request
  fingerprint conflict checks, 45-day expiry, and first-response
  preservation. PostgreSQL velocity intent counters use one transaction for
  the count and amount upserts; the adapter reads its spend snapshot through
  one connection.
- **Tests:** added English unit coverage for velocity transitions, cooldown
  expiry, open authorization compensation, derived-key enforcement, replay,
  body conflict, TTL expiry, and purge.
- **Decision:** the frozen DDL has no fingerprint column or reservation
  ledger. The idempotency adapter keeps its fingerprint in a namespaced
  JSONB envelope and the reservation adapter uses the stable purchase key
  plus existing purchases rows; no schema or contract files were changed.
- **Contracts touched:** none.

---

## 2026-08-29 — parallel phase verified; lint mask removed; gate-core merged
- **Why:** the audit coder's report claimed `ruff check .` green, but
  verification showed a `tool.ruff exclude` list had been added to
  `pyproject.toml`, masking pre-existing debt (`domain/` from `f4d9a69`)
  plus files that pass anyway or do not even exist (`test_webhooks.py`).
- **Done:** full review of P1–P5 against the brief (hash formula with seq
  excluded, genesis prev_hash, no floats, tail lock, guarded root annotation,
  KMS Ed25519, GCS `if_generation_match=0`, fail-closed verify, at-least-once
  relay, signed webhook) — verdict: approved with notes. Removed the exclude
  list (`cdedbc0`), fixed the domain debt at its origin on `dev2/gate-core`
  (`36cedb6`), and merged gate-core into this branch (devlog conflict
  resolved keep-both, as planned in the brief). Notes for the next
  iteration: relay transaction scope (SKIP LOCKED locks are released when
  the fetch transaction commits — single-instance relay plus sink dedupe
  covers P0), empty-ledger first-insert race under the tail lock, and
  `sign_root` annotates before witness publication (a crash in between is
  fail-closed detectable but needs manual recovery).
- **Tests:** `uv run pytest src/api/tests` → 60 passed, 5 skipped;
  `uv run ruff check` over tracked files → clean; docs-guard OK.
- **Decision:** none (review within existing #7/#10/#11/#15).
- **Contracts touched:** none.

---

## 2026-08-29 — P5: outbox relay with SKIP LOCKED
- **Why:** implement transactional event distribution via the Postgres outbox drained by `FOR UPDATE SKIP LOCKED` poller dispatching to idempotent sinks and signed merchant webhooks (decisions #10, #15, #19).
- **Done:** created `src/api/events/ports.py` (`OutboxEvent`, `Sink`, `OutboxStore`, `Clock`), `src/api/events/sinks_memory.py` (`InMemoryOutboxStore` with skip-lock simulation, `InMemorySink` with transient error injection), `src/api/events/webhook_signed.py` (`SignedWebhookPoster` signing canonical bodies with `RootSigner` evidence key headers `X-Aval-Signature`), `src/api/events/relay.py` (`OutboxRelay` poller with per-event error isolation, `PostgresOutboxStore` with `FOR UPDATE SKIP LOCKED`), and test suite `src/api/tests/test_events_relay.py` (6 unit and `@pytest.mark.db` tests verifying in-order delivery, retry on transient failure, signature verification, and concurrent worker deduplication).
- **Decision:** none new (implements #10, #15, #19).
- **Contracts touched:** none.

## 2026-08-29 — P4: chain verification use case (T9)
- **Why:** implement the full `Ledger` application service use cases (`append`, `sign_root`, `verify_chain`) supporting root signing and fail-closed chain verification against the external witness (decisions #7, #15, #19).
- **Done:** created `src/api/audit/service.py` (`LedgerService` orchestrating repo, signers, and witness) and test suite `src/api/tests/test_audit_verify.py` implementing test T9 (the live demo script: intact 25-event chain with 2 signed checkpoints verifies clean; 1-byte payload mutation at seq 7 breaks verification at seq 7; hash corruption at seq 15 breaks at seq 15; invalid root sig fails; divergent external witness fails; missing witness fails).
- **Decision:** none new (implements #7, #15, #19).
- **Contracts touched:** none.

## 2026-08-29 — P3: root signers + external witness
- **Why:** implement cryptographic root signing via Cloud KMS `EC_SIGN_ED25519` (non-exportable HSM key) and local Ed25519 for dev, accompanied by external root witness storage in versioned GCS buckets (decisions #7, #11, #15).
- **Done:** created `src/api/audit/signer_kms.py` (KMS `asymmetricSign` adapter with lazy imports), `src/api/audit/signer_local.py` (Ed25519 local keypair/PEM signer), `src/api/audit/witness_gcs.py` (GCS versioned bucket witness adapter with `if_generation_match=0`), `src/api/audit/witness_memory.py` (in-memory witness fake with tamper/deletion hooks), and tests in `src/api/tests/test_audit_signers.py` (unit sign/verify, corruption detection, immutability + `@pytest.mark.gcp` integration tests).
- **Decision:** none new (implements #7, #11, #15).
- **Contracts touched:** none.

## 2026-08-29 — P2: postgres ledger repository (append-only, tail lock)
- **Why:** provide persistent, append-only storage for the audit hash chain with concurrency serialization (tail-lock `SELECT ... FOR UPDATE`) to prevent chain forks, plus a testable in-memory fake with tamper injection (decisions #7, #19).
- **Done:** created `src/api/audit/ports.py` (`LedgerRepository`, `Clock`), `src/api/audit/repository_memory.py` (thread-safe fake with `tamper` hook), `src/api/audit/repository_postgres.py` (PostgreSQL driver with tail-lock atomic append, range queries, guarded `annotate_root`), and tests in `src/api/tests/test_audit_repository.py` (unit concurrency and chaining tests + `@pytest.mark.db` integration tests).
- **Decision:** none new (implements #7).
- **Contracts touched:** none.

## 2026-08-29 — P1: pure chain algebra (hashing + validation)
- **Why:** implement deterministic, append-only hash chain computations and pure validation rules without any I/O dependencies (decisions #7, #19).
- **Done:** created `src/api/audit/models.py` (`AuditEvent`, `ChainResult`, `RootCheckpoint`), `src/api/audit/hashing.py` (canonical JSON serialization with key ordering, no floats, UTC normalization, `compute_event_hash`, `compute_root_hash`), `src/api/audit/chain.py` (`validate_event`, `validate_chain`), and tests in `src/api/tests/test_audit_hashing.py` (12 unit tests covering determinism, sensitivity, payload mutations, hash corruptions, sequence gaps).
- **Decision:** none new (implements #7).
- **Contracts touched:** none.


---

## 2026-08-29 — domain/ brought up to the repo's ruff config
- **Why:** verification of the parallel-phase run revealed that the
  `domain/` extraction (`f4d9a69`) had never passed `ruff check` under the
  repo's own config (E/F/I/UP/B @ 100) — 26 real violations. The audit coder
  masked them with a `tool.ruff exclude` list instead of reporting the debt;
  that exclude is removed on `dev2/audit-evidence` and the debt is fixed here,
  at its origin.
- **Done:** import sorting (I001), `collections.abc` imports (UP035),
  `datetime.UTC` alias (UP017), `StrEnum` for the four string enums (UP042),
  unused imports (F401), and manual wraps for 12 long lines (E501) across
  `src/api/domain/{models,policy,idempotency,__init__}.py` and
  `src/api/tests/test_domain_gate.py`. Pure formatting/import hygiene — no
  public signature, value, or behavior change.
- **Tests:** `uv run pytest src/api/tests` → 22 passed (unchanged);
  `uv run ruff check src/api/domain src/api/tests/test_domain_gate.py` → clean.
- **Decision:** none.
- **Contracts touched:** none.

---

## 2026-08-29 — parallel phase brief issued: evidence & distribution (ledger + outbox)
- **Why:** a second coder can run in parallel with the decision-core brief
  without sharing a single code file — the ledger and the outbox relay are
  explicitly Dev 2's lane (decision 0019) and live in separate folders.
- **Done:** [`../plans/2026-08-29-dev2-parallel-audit-brief.md`](../plans/2026-08-29-dev2-parallel-audit-brief.md)
  on branch `dev2/audit-evidence` (created from this line): P1 pure chain
  algebra, P2 append-only Postgres repository with tail lock, P3 KMS
  EC_SIGN_ED25519 root signers + versioned-bucket GCS witness (lazy imports,
  fakes for tests), P4 verification use case = T9 (the judge-facing
  tamper-breaks-verification test), P5 outbox relay with FOR UPDATE SKIP
  LOCKED + signed webhook sink (#15). Lane discipline via allowlist diff
  check against `dev2/gate-core`; merge of both branches happens after both
  reports. Devlog collisions on merge are expected (append-only, keep both).
- **Decision:** none new (executes #7/#10/#11/#15).
- **Contracts touched:** none.

## 2026-08-29 — Dev 3 surface archive (stash) dropped
- **Why:** Dev 3 is rebuilding the API surface on their own line, so the
  stashed coder-run copy served no one on this branch — and it was flagged
  as noise/risk in our own coder brief (a coder could apply it by mistake).
- **Done:** `git stash drop` of the "dev3 surface archive" entry; stash list
  now empty. Nothing in our lane depended on it (the domain core was already
  extracted and committed in `f4d9a69`). Brief updated to remove the stash
  warnings; DoD now asserts an empty stash list instead.
- **Decision:** none (housekeeping within the 0019/0022 lane boundaries).
- **Contracts touched:** none.

## 2026-08-29 — Dev 2 coder brief issued (decision core completion)
- **Why:** handover done; the four pending pieces of our lane need a coder
  run correctly scoped this time — decision core only, zero API surface.
- **Done:** [`../plans/2026-08-29-dev2-coder-brief.md`](../plans/2026-08-29-dev2-coder-brief.md)
  — 5 closed commits: C1 velocity store (Postgres + fake, atomic upserts),
  C2 idempotency store (T19 store side), C3 verify path with atomic
  reservation + escalation flow (re-gate, never bypass; lazy expiry
  fail-closed; outbox same-tx), C4 T1 property-based extension (Hypothesis),
  C5 db-marked integration. Lane discipline enforced by DoD: forbidden-files
  diff check against router/main/schemas/config/agent/mocks/contracts;
  stash must stay unapplied; no push.
- **Decision:** none new (executes 0019/0021/0022 + #1/#4/#5/#10).
- **Contracts touched:** none.
- **Open questions:** coder report pending; `escalations.level` stays in
  `diff` JSONB (a real column would need a decision record).

## 2026-08-29 — extracted Dev 2 domain core from the coder run (branch `dev2/gate-core`)
- **Why:** the coder executed the full-P0 brief (all lanes) on the Dev 3
  branch; Dev 3 is building the API surface themselves, so we took only what
  is ours per decisions 0019/0022 and deleted that branch.
- **Done:** extracted `src/api/domain/` — `policy.py` (PolicyGate with
  R-PRICE gate check, R-BURST escalate/cooldown/auto-suspend, R-STEPUP 0.7/0.8
  thresholds with L3/L3+ TTLs 120/300 s, gold rule encoded as
  verdictive-vs-corroborative sets, fail-closed UV stub, re-gate escalation
  resolution), `models.py`, `idempotency.py` (HMAC(jti) derivation,
  fingerprinted reuse validation, 45-day retention). Verified self-contained
  (stdlib only). Added `tests/test_domain_gate.py` — seed cases of T20/T22/T23
  + gold rule + R-IDEM invariants (21 tests, green). The committed
  `db/schema.sql` already carries our migration [2] risk tables.
- **Decision:** none new (executes 0020–0022). Branch
  `dev3/fraud-transaction-research` deleted at Dev 2's request after
  extraction; its docs history (research, plan, decisions 0020–0022,
  contracts v1.1) survives in this branch's history. The Dev 3 surface work
  (webhooks/rail/yuno-mock/ports) was NOT discarded: it is archived in a
  labeled git stash for whoever wants it.
- **Contracts touched:** none (v1.1 untouched).
- **Tests I own:** T20/T22/T23 seeds green. Still pending in our lane:
  full property-based T1 extension over the new rules, Postgres wiring of
  `velocity_counters`, integration of the gate into the verify path, and
  T19's store/repository side.
- **Open questions:** none.

## 2026-08-29 — workstream opened at M0 freeze
- **Why:** contracts v1.0 are frozen; this log exists so nobody re-solves what Dev 2 already solved.
- **Decision:** none yet. Starting points:
  [`../../DECISIONS.md`](../../DECISIONS.md) #1 (no model in enforcement),
  #5 (JSON Logic), #7 (hash chain), #10 (outbox). Interfaces:
  [`../../contracts/schemas.md`](../../contracts/schemas.md) §3
  (`PolicyGate`, `Ledger`).
- **Contracts touched:** none.
- **Tests I own:** T1 (invariant, property-based), T4, T5 (race), T6 (TOCTOU),
  T9 (hash chain), T10 (JsonLogic), T13 (demo-as-code); T7 and T18 jointly.
- **Open questions:** none.
