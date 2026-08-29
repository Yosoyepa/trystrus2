# Decision log

One entry per real choice. Write it when you make the call, while you still
remember the alternative. This is a graded deliverable and the judges have said
the technical defence counts as much as the demo.

Format: what we chose, what we rejected, why, and what our choice still does not
solve. That last line is the one that scores. A team that names its own limits
reads as a team with judgment.

---

## 1. No model runs inside the enforcement path

**Chose:** A deterministic rules engine. Same input, same answer, every time.

**Rejected:** Letting the agent's model judge whether a purchase fits the mandate.

**Why:** The judges will operate this live and try to break it. A decision that
can vary between two identical requests is not a control, it is a suggestion.
It also answers the challenge's own question about what happens when the agent
hallucinates: the model can hallucinate a proposal, and the proposal still has
to pass a check it cannot influence.

**Does not solve:** The model still chooses what to propose, so a manipulated
agent wastes the buyer's time and fills the log with refusals.

---

## 2. What a mandate is

**Chose:** A signed SD-JWT (RFC 9901) shaped like an AP2 mandate: typed claims
for limits (`max_per_txn`, `total_budget`, `max_txn` count/period), scope
(categories, merchants), a validity window, a `payment_method_ref` that is an
opaque token id, the agent's public key in `cnf.jwk`, and the conditions as
JSON Logic.

**Rejected:** a row in our database (verifiable by nobody but us); a plain JWT
(no selective disclosure, no key binding); a delegation-chained JWT (we have one
delegation hop — a person to an agent — not a graph); a capability token such as
a biscuit or macaroon (clever attenuation, zero ecosystem); a full W3C
verifiable credential in JSON-LD (interop machinery we will not use in a
weekend).

**Why:** Between Sep 2025 and 2026 the industry converged on exactly this
object: Google's AP2 (now stewarded with the FIDO Alliance) chains signed
mandates Intent → Cart → Payment; Google/Shopify's UCP reuses AP2 mandates;
Mastercard Agent Pay and Visa's agent tokens assume the same shape exists.
Building the standard's shape means our defence is "we implemented the standard
and closed its two open gaps" (live revocation, dispute resolution), not "we
invented something". SD-JWT is a published RFC, the Python library is already in
our venv, and key binding gives proof-of-possession for free.

**Does not solve:** The mandate says what may be bought, not what was bought —
each purchase still needs its own signed intent (see #9).

---

## 3. How the person signs it

**Chose:** A passkey (WebAuthn, user verification required). The ceremony
challenge is the canonical hash of the mandate, so the biometric gesture signs
the exact permission, not a session. Used to create, to change limits, and to
revoke.

**Rejected:** a key held in the browser via WebCrypto (worse recovery story,
worse UX); a server-side key we hold on their behalf (then we could forge
mandates — this kills non-repudiation, the property we exist to provide).

**Why:** An agent cannot complete a WebAuthn ceremony — it requires a physical
gesture on an authenticator. That asymmetry is the proof of human intent, and it
is the same direction the industry took: FIDO's Verifiable Intent framework with
Mastercard, and the Agentic Authentication working group Google donated AP2 to.

**Does not solve:** Lost-passkey recovery (out of scope for the demo). Also pins
us to owning a real domain: passkeys do not work on `*.run.app` (public suffix
list), so the domain is a day-0 purchase.

---

## 4. How revocation reaches a purchase already in flight

**Chose:** A synchronous check at pay-time. The merchant's charge path calls our
verify endpoint inside the same transaction that reserves budget against a
guarded `UPDATE` (status must be active, remaining limit must cover the amount —
zero rows updated means refuse). Revocation additionally DELETEs the payment
token at the rail, so the next attempt fails twice: once in our state, once at
PayPal.

**Rejected:** short-lived spend authorisations that stop being minted (adds
latency and minting races for no demo value); a published status list the
merchant polls (the honest production answer — W3C bitstring status lists — but
a poll adds a staleness window we would have to defend on stage).

**Why:** The judges revoke live and watch the next attempt fail. A push can be
lost and a cache can be stale; a same-transaction read cannot. This closes
time-of-check-to-time-of-use by construction. Target latency ≤ 2 s.

**Does not solve:** A purchase that already captured before revocation is a
refund/dispute case, not a revocation case — different flow, same audit trail.

---

## 5. Where the rules live

**Chose:** JSON Logic for the conditions, embedded in the signed mandate, plus a
declarative limits block (counters, budget) — both evaluated by an in-process
deterministic gate that wraps the only path to money.

**Rejected:** a hand-written typed evaluator (re-invents JSON Logic, worse); a
model judging fits (see #1); Cedar (typed and pleasant, but expressions are
strings — hard to sign canonically and to render in an audit UI); Open Policy
Agent with Rego (another running piece for thirty lines of policy — kept as an
optional showcase, not a dependency).

**Why:** Rules that are JSON are data. The same object the person signs is the
one the gate evaluates and the auditor renders — end-to-end traceability of the
rule itself. Deterministic, no I/O, property-testable: our first test is the
invariant that no randomly generated out-of-mandate intent ever approves.

**Does not solve:** Conditions can only reference offer fields and time.
Anything richer is a schema change, deliberately.

---

## 6. How the merchant verifies without trusting us

**Chose:** The merchant verifies the cryptography itself: the mandate SD-JWT
against our published JWKS (`/.well-known/jwks.json`), the purchase intent
against the agent key inside the mandate's `cnf.jwk`. It then calls our verify
endpoint for state, limits and reservation — but the signatures it can check
offline, without us.

**Rejected:** the merchant calls our API and believes the answer (then we are
the single point of trust — the exact thing this challenge asks to remove); a
shared secret (proves nothing to a third party auditor).

**Why:** An impersonated agent without the private key cannot produce a valid
intent — the impersonation case dies at the signature, before any call to us.
This is what makes the verification meaningful for a stranger, which is the
challenge's definition of verification.

**Does not solve:** JWKS freshness and rotation (we publish current + previous
key with 24 h grace; a hard-cutover story is future work).

---

## 7. The shape of the audit log

**Chose:** An append-only hash chain — each event row carries `prev_hash` — with
signed checkpoints at the close of each purchase. Roots are signed by Cloud KMS
(`EC_SIGN_ED25519`, the key never leaves Google) and copied to a versioned,
publicly readable bucket.

**Rejected:** ordinary rows in the database (mutable, proves nothing); a real
transparency log in the Trillian/RFC 6962 line (Merkle inclusion proofs we will
never show a judge).

**Why:** The auditor view recomputes the chain live; flipping one byte in the
database breaks verification on stage. The external witness is the load-bearing
detail: a root that only lives in our database proves nothing against us — the
copy outside our perimeter is what turns tamper-evidence into accountability.

**Does not solve:** Who guards the guards — a history of key compromise. Stated,
not solved.

---

## 8. Payments (updated: mock → PayPal sandbox)

**Chose:** PayPal sandbox. The person approves a vaulted payment token once
(setup token → PayPal approval → payment token); captures afterwards are
server-to-server with `vault_id` and no buyer interaction; disputes are created
and adjudicated through the sandbox Customer Disputes API; token DELETE is the
rail-side kill switch.

**Rejected:** a pure internal mock (our original call, correct when nothing
else was reachable); Stripe in test mode (fine, but not the direction we were
pointed at); x402/Coinbase (machine-native and genuinely impressive — HTTP 402,
gasless EIP-3009, settlement in seconds — but irreversible: you cannot demo a
chargeback on-chain, and reversibility is the heart of this challenge).

**Why:** We have no sandbox access to the sponsor's platform. PayPal's sandbox
is instant, free, and does the two things a mock never could: a real dispute
object for the bonus flow, and a real "payment fails after revocation" at the
rail. The original mock reasoning (test-mode keys and tunnels cost hours that
buy no marks) turned out to be false here: ~6 endpoints, direct REST, the
official Python SDKs are deprecated anyway. The person approves the instrument
once inside PayPal; the agent only ever sees the opaque token id.

**Does not solve:** Live-mode gating (vaulting needs eligibility review in
production); amounts are USD only (COP is not a supported currency code).

---

## 9. Agent identity

**Chose:** An Ed25519 key pair per agent. The mandate binds the public key
(`cnf.jwk`); every purchase is a canonical-JSON intent (RFC 8785) signed as a
detached JWS with that key, carrying a nonce and a ≤ 120 s expiry.

**Rejected:** an API key (a bearer token — whoever steals it simply IS the
agent); mutual TLS (transport identity, not per-transaction intent); signed HTTP
sessions with keys at a well-known directory (that is what we do, but per intent
rather than per session — a session key that leaks authorizes until it expires;
an intent signature authorizes exactly one purchase).

**Why:** Separates agent identity from human identity, which the challenge asks
for explicitly. Gives replay protection (nonce/jti uniqueness enforced at the
verify endpoint). Makes impersonation a testable failure: a cloned agent
without the key produces `INVALID_PROOF_OF_POSSESSION` — we demo that.

**Does not solve:** Key custody for real-world agents. Ours live in Secret
Manager; a production agent would hold its own.

---

## 10. No message broker — a Postgres outbox

**Chose:** The queue from the whiteboard becomes an outbox table written in the
same transaction as the business change, drained by a `FOR UPDATE SKIP LOCKED`
poller that feeds SSE, the bot and the merchant webhook.

**Rejected:** RabbitMQ (another container to keep alive at 3 a.m. before
judging); Pub/Sub (managed, free tier, but a second system to reason about for
three consumers); Postgres `LISTEN/NOTIFY` (does not survive serverless — the
connection dies when Cloud Run scales to zero).

**Why:** Atomicity a broker cannot offer: the event and the business change
commit together or not at all. Our audit lives in the database — consistency
here is the argument, not a compromise.

**Does not solve:** Fan-out beyond a handful of consumers. Pub/Sub is the
documented growth path, an explicit decision not an omission.

---

## 11. Platform: Cloud Run + Cloud SQL

**Chose:** Everything on Cloud Run in one region (`southamerica-east1`),
Postgres on Cloud SQL (`db-f1-micro`, no public IP, unix socket), secrets in
Secret Manager, evidence keys in KMS, roots witnessed to a versioned GCS bucket.

**Rejected:** GKE (operational cost we cannot pay in three days); Compute
Engine (servers to patch); Cloud Functions (no streaming SSE, no long-lived
state); AlloyDB (ten times the cost for nothing we need).

**Why:** One `gcloud run deploy --source` per service from day zero; SSE
supported natively; ~US$12–28/month fully inside trial credits. São Paulo is the
closest region to Bogotá (~50–70 ms) and carries every service we need.

**Does not solve:** Multi-region, HA, real infrastructure-as-code — gcloud
scripts now, Terraform named as roadmap.

---

## 12. Microservices with the frontend as its own service

**Chose:** Independently deployable services — `web`, `kernel`, `agent` (plus
the watcher job), `merchant` — where the frontend consumes only published
contracts. Its TypeScript types are generated from the same OpenAPI file the
backend derives its Python models from.

**Rejected:** a modular monolith (one broken module takes the whole demo down,
deploys couple); sharing one frontend codebase across three backend devs (the
web app becomes the coordination hotspot — shared shell, shared routing,
cross-owned pull requests).

**Why:** Four people building asynchronously need deployment boundaries that
match ownership boundaries. The web dev never opens Python; the backend devs
never open React; contracts are the only shared surface, and a failure degrades
the demo instead of killing it.

**Does not solve:** Contract discipline becomes load-bearing — the day-0 freeze
rules in `docs/PLAN-PARALELO.md` exist because of this entry.

---

## 13. Human in the loop channel

**Chose:** Telegram primary — inline keyboard, and the escalation message
itself mutates from "Pending…" to "APPROVED by judge X at 14:32" so the state
change is visible on phone and projector at once. WhatsApp pre-warmed as a
secondary. Escalation timeout 120 s → auto-deny, fail closed.

**Rejected:** Slack Block Kit (needs every judge inside a workspace); email
(too slow); web-only approvals (works, less impressive live).

**Why:** Judges operate from their own phones. A fail-closed timeout means
silence never approves anything — the same rule production agent products use
(OpenAI Operator asks and waits; it does not assume).

**Does not solve:** WhatsApp business-initiated messages require Meta-approved
templates, so WhatsApp stays a pre-warmed secondary, never the demo's spine.

---

## 14. The model

**Chose:** Vertex AI Gemini or OpenAI, decided on day 0 by available credits and
measured latency. The enforcement path has no model (see #1), so this choice
affects the quality of proposals, never safety.

**Rejected:** Gemini API free tier (cut to ~20 requests/day in Dec 2025 —
unusable for a live demo).

**Does not solve:** Token spend is ours, not the buyer's — the mandate caps the
buyer's money, not our inference bill.

---

## 15. Where the keys live

**Chose:** Two honest tiers. Mandate-issuing keys as PEM in Secret Manager (the
SD-JWT library wants local key material; JWKS publishes the public half with
`kid`-versioned rotation). Audit roots and webhooks signed by Cloud KMS
`EC_SIGN_ED25519` — non-exportable, the service calls `asymmetricSign` and the
key never leaves Google.

**Rejected:** keys in environment variables or the database; everything in KMS
(breaks the signing library for no demo value).

**Why:** Operational keys rotate cheaply; evidence keys are the ones whose
custody we will be cross-examined on, so those are the ones a cloud HSM holds.
Costs cents per month.

**Does not solve:** Nothing a hardware token in the judges' hands would not
improve. Stated for the cross-examination.

---

## 16. Agent orchestration without LangGraph

**Chose:** A hybrid adaptation — keep the four critical ideas that fit us
(explicit graph as control flow with the LLM confined to the propose node,
checkpointing to Postgres via `agent_runs`, interrupt-before-pay as an
`await_human` node resumed by escalation resolution, tools as a bounded
contract) and implement them in ~150 lines of framework-free Python. Full
record: `docs/decisions/0016-agent-orchestration-without-langgraph.md`.

**Rejected:** LangGraph the framework (learning curve, checkpointer setup and
framework debugging concentrated in one dev over a 2.5-day build — the exact
bottleneck we were avoiding); OpenAI Agents SDK (vendor coupling for no
safety gain); no orchestrator at all (unauditable, unresumable).

**Why:** What we needed from LangGraph was three behaviors, not a package.
The graph is code we can read in one sitting, its state is one table we can
SELECT, and its resume semantics are the escalation contract we already
froze. Every graph transition can become an audit event — the agent's
trajectory joins the evidence chain.

**Does not solve:** We own the orchestrator's bugs (graph-loop, resume
races); no free integration ecosystem. Wrapping the same nodes in LangGraph
later is the documented path back.

---

## 17. Every change documents itself (devlogs + decisions, enforced by CI)

**Chose:** A repository structure that makes documentation unavoidable
rather than encouraged: one append-only devlog per workstream under
`docs/devlogs/`, full decision records under `docs/decisions/` (this file
stays the short index), and a CI guard — `scripts/docs-guard.sh` — that
rejects a PR changing code without a devlog entry, or changing a frozen
contract without a decision record. Full record:
`docs/decisions/0017-documentation-protocol.md`.

**Rejected:** trusting discipline (documentation dies on day 2 of a
hackathon); a wiki (drifts from the code); docs written at the end (then
they are fiction, not record); unenforced conventions.

**Why:** Four people plus AI agents build asynchronously against frozen
contracts. The failure mode is duplication and lost context: two streams
re-solving the same problem because neither knew the other had. A devlog
entry costs two minutes; the guard makes skipping it impossible to merge.
For a new agent — AI or human — one file contextualizes the whole workstream.

**Does not solve:** Documentation quality. The guard checks presence, not
truth. Reviews still have to read.

---

## 18. Coherence pass: naming and channels aligned to the decided build

**Chose:** Align every document to the decisions: channels are web / Telegram
(primary) / WhatsApp (secondary) per #13; the "not decided yet" and "still
open" closers became "Since decided" with pointers; FOUNDATION gained a
whiteboard-component → build mapping; the contract's issuer example is
`api.aval.example`; the sources footer points at `docs/decisions/`. Full
record: `docs/decisions/0018-coherence-pass-naming-and-channels.md`.

**Rejected:** keeping the foundation docs as untouched history (README sends
every newcomer and AI agent to them first — "not decided yet" about a frozen
contract is an active hazard); rewriting the whiteboard voice entirely
(annotating beats replacing).

**Why:** In a repo where agents contextualize themselves from the docs,
coherence is load-bearing. And this entry exists because the docs-guard
demands a decision record for any contract change — including a one-line
example rename. That is the protocol working as designed.

**Does not solve:** `docs/fig1-3.png` still show the original whiteboard
wording — kept as artifacts of the starting point.

---

## 19. Four workstreams cut by capability, not by architectural layer

**Chose:** Dev 1 agentic (agent + watcher: own graph, signed intents,
Presidio, injection suite) · Dev 2 fraud, contracts, idempotency (gate,
verify + atomic reservation, saga + compensation, idempotency keys,
hash-chained ledger, outbox) · Dev 3 API backend (mandates + passkeys,
state machine, escalations, catalog + MCP tools, checkout, PayPal rail,
webhooks) · Dev 4 front & platform (three consoles, bot, GCP infra, CI/CD).
The C1/C2/C3 subdivision dissolves: brain → Dev 1, money and store → Dev 3.
Full record: `docs/decisions/0019-workstreams-cut-by-capability.md`.

**Rejected:** one-service-per-dev (five deployables, four devs); the old
layer cut (its symptom was C's overload, which C1/C2/C3 only patched);
keeping the subdivision (a patch for a cut that no longer exists).

**Why:** Capability lanes match how failures are investigated on demo day:
one dev owns every anti-fraud invariant end to end, one owns everything the
agent does, one owns every API surface, one owns what the judges touch.
Contracts, milestones and tests are untouched — parallelism never depended on
which dev held which component.

**Does not solve:** Dev 3 is the heaviest lane (identity + store + rail);
mitigations pre-agreed — catalog detaches to Dev 1, checkout to Dev 2 after
M1, mandate crypto never moves. Devs 2 and 3 share the kernel deployable, so
folder discipline is load-bearing.

---

## 20. Yuno behind PaymentRail: mock in the demo, real integration out of scope

**Chose:** Extend `PaymentRail` with `get_status()` (non-terminal states)
and `respond_dispute()` (disputes are inbound on every rail); deprecate
`open_dispute`. Add `YunoMockRail` built from Yuno's documented contract
(auth, `X-Idempotency-Key` four behaviors, faithful state machine, webhook
v2 with real HMAC, injectable `mock_mode`); rail selection via
`AVAL_RAIL=paypal|yuno_mock`. The demo runs PayPal as the real rail and the
mock as the sponsor story — the real Yuno integration is descarted (no
sandbox credentials; the 48 h gate cannot be met). Full record:
`docs/decisions/0020-yunorail-adapter.md`.

**Rejected:** real Yuno now (credential provisioning on the critical path);
Yuno as primary rail (same problem + PCI/Testing Gateway enrollment);
ignoring Yuno (it is the sponsor).

**Why:** The protocol was designed for the swap: the kernel never knows who
charges. The mock is not throwaway — when credentials exist, real `YunoRail`
is a configuration change. And the two gaps Yuno exposed are real for PayPal
too (async captures, inbound disputes).

**Does not solve:** We cannot claim "running on Yuno" — only "integrated
against the documented contract, demonstrated via a faithful mock". The doc
inconsistencies found (auth header casing, production URL, idempotency TTL)
stay untested until real credentials. Fase 4 roadmap.

---

## 21. Escalation TTL by level: 120 s standard, 300 s with passkey UV

**Chose:** L3 (standard bot approval) keeps 120 s fail-closed. L3+
(amount ≥ 0.7 × `max_per_txn`, budget ≥ 80 %, first escalation, or fresh
agent key) gets 300 s with RFC 9470 `max_age` semantics and requires WebAuthn
`userVerification:"required"` signing the hash of the diff. Both levels fail
closed — silence never approves. Full record:
`docs/decisions/0021-escalation-ttl-by-level.md`.

**Rejected:** uniform 120 s (the UV channel hop would time out, turning a
security feature into DoS on legitimate buys); uniform 300 s (doubles
exposure for the common case); minutes/hours cooling-off (demo cannot afford
— Fase 4).

**Why:** Fail-closed is the invariant; the window is a per-level UX
parameter. Judges keep the instant live-revocation story while the passkey
ceremony gets a realistic budget.

**Does not solve:** Coercion inside the 300 s window is mitigated
(out-of-band, diff, UV biometrics), not eliminated. Channel-hop latency must
be measured at M3 — if it exceeds 300 s, retune thresholds, not the TTL.

---

## 22. P0 fraud-control ownership split and tests T19–T25

**Chose:** Dev 2 takes R-PRICE, R-BURST, R-STEPUP (the gate's rules) +
risk-table migrations; Dev 3 takes R-IDEM, R-WEBHOOK, R-EVIDENCE, rail risk
metadata, the Yuno mock + `webhook_archive` migration; Dev 1 the adversarial
offer strings; Dev 4 the diff + UV deep-link UI. Tests T19–T25 assigned per
lane; T25 (integral attack script) doubles as the demo rehearsal gate. Full
record: `docs/decisions/0022-p0-ownership-split.md`.

**Rejected:** all controls to Dev 2 (rail-surface work would re-create the
overload 0019 mitigated); all to Dev 3 (would split the gate's anti-fraud
invariant across lanes); deferring until M1 (F1.1/F1.3 start immediately).

**Why:** Same principle as #19: an attack that survives the gate is Dev 2's
incident; one that arrives through the rail's surface is Dev 3's. The gold
rule travels with the gate: corroborative signals only ESCALATE, verdictive
ones REJECT.

**Does not solve:** P1 hardening has owners in the plan but no schedule —
post-event by definition. T25 depends on everything else being green.
