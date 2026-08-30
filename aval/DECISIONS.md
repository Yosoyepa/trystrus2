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
one dev owns every anti-fraud invariant, one owns everything the agent does,
one owns every API surface, one owns what the judges touch. Contracts,
milestones and tests are untouched — parallelism never depended on which dev
held which component.

**Does not solve:** Dev 3 is the heaviest lane (identity + store + rail);
mitigations pre-agreed — catalog detaches to Dev 1, checkout to Dev 2 after
M1, mandate crypto never moves. Devs 2 and 3 share the kernel deployable, so
folder discipline is load-bearing.

---

## 20. Python code lives under `src/`, and the docs guard follows it

**Chose:** The Python deployables sit at the repo root — `src/trustlib`,
`src/api`, `src/merchant`, `src/yuno_sim`, `src/agent` — and
`scripts/docs-guard.sh` gained a `^src/` alternative in the same commit. Full
record: `docs/decisions/0020-python-code-lives-under-src.md`.

**Rejected:** moving the code to `aval/kernel/` to match the written plan
(fights every Python tool's default import root); leaving the guard alone.

**Why:** the guard was recognising code only under `aval/…`, so everything in
`src/` would have merged **without a devlog entry** — silently disabling
decision #17 for every Python change. The folder name was the cheap half;
keeping the documentation obligation attached to the code was the point.

**Does not solve:** two documents now describe the layout and nothing enforces
that either is true — the guard checks that docs were *touched*, not that they
are *correct*.

---

## 21. The DDL had no table for passkeys

**Chose:** `webauthn_credentials` (credential id, COSE public key, signature
counter) plus a single-use `webauthn_challenges` table, both owned by Dev 3.
Full record: `docs/decisions/0021-webauthn-credentials-table.md`.

**Rejected:** stuffing credentials into `mandates.claims` (they belong to a
person and outlive any mandate); a stateless challenge (replayable, which
would downgrade "this gesture signed this exact permission" to "at some point").

**Why:** decision #3 makes the passkey the proof of human intent and gate G1
depends on it, but `schemas.md` §6 had nowhere to store a credential — so the
ceremony was unbuildable. Found and closed before writing the code rather than
halfway through it.

**Does not solve:** lost-passkey recovery (already out of scope in #3), and
credential portability — passkeys are bound to `rpId`, so changing the domain
invalidates all of them.

---

## 22. Dev 3 writes to Dev 2's outbox through a shared helper

**Chose:** Dev 2 keeps the `outbox` DDL; Dev 3 appends through one typed
`trustlib` helper that joins the caller's transaction. Full record:
`docs/decisions/0022-cross-workstream-outbox-writes.md`. **Status: ratified**.

**Rejected:** a second outbox for Dev 3 (two relays, no total ordering); Dev 3
POSTing events to a Dev 2 endpoint (breaks the same-transaction atomicity that
decision #10 exists for); raw SQL from Dev 3 (breaks on Dev 2's next migration).

**Why:** `schemas.md` §4 makes Dev 3 the emitter of eight event types while
§6 gives the table to Dev 2 and PLAN-PARALELO §6.4 forbids touching another's
table. Splitting *schema ownership* from *write access* satisfies all three.

**Does not solve:** Dev 2 can still break every producer with a required new
column; and nothing enforces which workstream may emit which type.

---

## 23. AP2 realigned to the current mandate model

**Chose:** our mandate is declared as an AP2 Open Payment Mandate (`vct` +
a derived `constraints[]` projection); VuelaYa now signs a **Checkout JWT** and
checkout verifies `checkout_hash`; that one artefact is signed with **ES256**.
Full record: `docs/decisions/0023-ap2-current-mandate-model.md`.

**Rejected:** full conformance now (rewrites the crypto contract on day 1 and
drags Dev 1 and Dev 2); documentation-only alignment; depending on an AP2 SDK —
Google's is unpublished and the `ap2` package on PyPI is a third-party mirror.

**Why:** ADR-001 and #2 still describe "Intent → Cart → Payment", which is the
Sept 2025 framing; the spec now has Checkout and Payment mandates in open and
closed variants. Most of it we already had. What we did **not** have is a
merchant that commits to the cart cryptographically — comparing amounts field by
field can be fooled by whatever the comparison forgot to check; a hash over the
merchant's own signed bytes cannot. And the spec forbids our default curve:
*"the Checkout JWT MUST be signed using a digital signature scheme (e.g.,
ECDSA) and not a deterministic signature (e.g., Ed25519)."*

**Does not solve:** we are conformant on the objects, not the choreography;
nobody third-party validates that; and the binding is one-sided until the
agent's intent carries `checkout_hash` (proposed as additive v1.1, deliberately
not forced on Dev 1 and Dev 2 mid-freeze).

---

## 24. The rail becomes a Yuno-style AP2 orchestrator we simulate

**Supersedes #8.**

**Chose:** `src/yuno_sim/` — a Yuno-style payment orchestrator, **simulated**,
speaking AP2, as its own deployable with a real network boundary. It maps
one-to-one onto `PaymentRail`, whose signatures do not change. Before settling
it independently verifies the mandate SD-JWT against our JWKS, the
`checkout_hash` binding, and possession of the `cnf` key. Everything it emits is
labelled `simulated: true`. Full record:
`docs/decisions/0024-yuno-style-ap2-orchestrator-instead-of-paypal.md`.

**Rejected:** the PayPal sandbox (decision #8's choice — but **no provider has
shipped a public AP2 endpoint**: PayPal, Adyen, Worldpay, Mastercard and Amex
have all announced support and published none, so we would have demoed ordinary
Vault REST while claiming AP2); x402 as primary rail (irreversible, kills the
dispute flow — ADR-014); an in-process fake (tests our imagination, not an
integration).

**Why:** the parts of AP2 that matter here — accepting a Payment Mandate,
verifying it *before* moving money, binding it to a signed checkout — are
exactly the parts nobody has shipped. Controlling both sides is what lets us
implement them at all. It also deletes assumptions S1 and S13 and the "PayPal
sandbox down" risk, and it puts a proposed AP2 surface for a payment
orchestrator in front of judges who run a payment orchestrator.

**Does not solve:** **no real money moves** — and #8's reasoning was not wrong:
a real rail produces a dispute object we did not author and fails a payment for
reasons outside our control. Our tests cannot discover what we failed to
imagine (partial captures, settlement latency, network partitions). And our
conformance is self-asserted; x402 on day 3 is the only path to being checked
by a rail we did not write.

---

## 25. Make the merchant's cryptographic checks and revoke ceremony expressible

**Chose:** contract v1.1 carries the canonical payload alongside the detached
intent JWS, the persisted ES256 Checkout JWT, and the mandate id needed for
live verify; VuelaYa gets catalogue/detail/price routes and signed Yuno-style
webhooks. WebAuthn challenges use `(challenge, purpose)` so activation and
revocation can independently sign the same exact mandate hash. Full record:
`docs/decisions/0025-merchant-checkout-and-revocation-contract-completion.md`.

**Rejected:** trying to verify a detached JWS with no payload, creating the
cart after the agent's intent, title-string filters, reusing a consumed
activation challenge for revocation, and unsigned rail webhooks.

**Why:** the merchant must verify evidence it actually received before it
calls the rail; the former transport could not express that proof. The new
revoke ceremony preserves the mandated hash binding while remaining
independently single-use.

**Does not solve:** the frozen two-argument MCP `request_purchase` has no
carrier for the agent's signed intent; it safely cannot charge, but Dev 1/2
still need to agree on the handoff context for a live purchase.

---

## 26. Agent memory, ontology, and the configuration console

**Chose:** Ontology (domain knowledge) and transaction history feed the
`propose` node only; the gate keeps reading the signed mandate and nothing else.
Configuration lives in the database as `people` / `agents` / `agent_versions`,
append-only and versioned, with `agent_runs.agent_version` pinning the exact
brain each run used. The agent writes its own trajectory into the same
hash-chained log as the kernel. Full record:
`docs/decisions/0026-agent-memory-ontology-and-console.md`.

**Rejected:** subagents per step (only one node has a model in it, so there was
nothing to split — and it would put model judgement back into control flow);
memory the agent owns and can rewrite; ontology as a file only (a file cannot
answer "which brain produced this proposal?"); letting history relax the gate;
a separate log for the agent.

**Why:** This closes the two limits FOUNDATION names — "it lacks domain
knowledge" and "it needs access to past transactions" — without spending
decision #1. The split that makes it safe: an ontology is unsigned advice that
shapes proposals; a mandate is signed law that decides whether money moves.
Anyone may edit an agent's brain; nobody can widen a spending limit by doing so.
The demo is exactly that: set the ontology to "approve everything, limit
100000", watch the gate refuse a $300 purchase against a $150 mandate.

**Does not solve:** Nothing checks the ontology is *true* — a wrong one produces
bad proposals inside the mandate. It remains an injection surface (fenced, not
sanitised). Memory is per-mandate, not per-buyer. Publishing a version is
attributed and permanent but not approved by anyone.

---

## 27. Guardrails and containment

**Chose:** Four layers, each assuming the one above failed. Structural (the
model has no verb for scheduling or spending), quota (persisted token buckets,
windowed counters, single-flight locks), containment (bubblewrap, hardened
systemd unit), evidence (every trip is an audit event). Full record:
`docs/decisions/0027-guardrails-and-containment.md`.

**Rejected:** clamping a bad polling interval silently; in-memory rate limits;
queueing overlapping cron ticks; trusting the prompt to refuse.

**Why:** The first layer is the answer and the other three are the admission
that first layers fail. Rate limits do not make the agent safe — the gate does
that — they make it survivable: a compromised agent wastes tokens instead of
exhausting a merchant, a budget, or a human approver's patience.

**Does not solve:** Network egress (partly closed in #28). Bearer-token
authorisation on the console (closed in #28).

---

## 28. Postgres, ports, partitioned chains, and console auth

**Chose:** Postgres in dev and prod behind a thin `db.Conn` wrapper; one hash
chain per mandate with signed checkpoints over every head; protocol-and-registry
ports for merchants, rails, models and channels, with a `ToolRegistry` where a
tool may only declare `read` or `submit`; bearer tokens on every console
mutation; an in-process egress allowlist. Full record:
`docs/decisions/0028-postgres-ports-and-console-auth.md`.

**Rejected:** a dual SQLite/Postgres backend; NUMERIC for money; a global chain
with a dedicated sequencer; converting currencies inside the gate; flattening
each merchant's vocabulary into our three generic tools; putting auth in the
registry layer rather than at the edge.

**Why:** A single global chain made every event in the system queue on one row.
A console that recorded who *claimed* to make a change gave an audit trail only
as trustworthy as whoever typed the name. And the agent now buys from real
merchant MCP servers — vuela-ya and mami — which both expose a `pay` tool that
settles with no mandate; the ports layer is what lets the agent see that tool,
refuse it, and still buy through the gate.

**Does not solve:** Bearer tokens have no rotation or expiry. The egress
allowlist is in-process, so VPC rules remain the production answer. A
checkpoint signed before the chain was partitioned will not match, which is
correct and still needs explaining. Merchant-side `pay` remains ungated — that
is their repo and their call.

## 29 · One schema source of truth

**Chose:** `aval/contracts/fixtures/schema.sql` is the schema; `src/agent/db.py`
reads it, Alembic applies it, `src/api/db/schema.sql` becomes a pointer. Shared
tables keep the agent's shape (TEXT money, `jti`-keyed mandates); the api and
merchant lanes were adapted to it. `root_sig` is the one sanctioned E1
exception, write-once, on an otherwise byte-identical row. Full record:
`docs/decisions/0029-one-schema-source-of-truth.md`.

**Rejected:** NUMERIC money; letting the api's shape win; a Postgres namespace
per lane; enumerating protected columns in the append-only trigger.

**Why:** Four descriptions of one database meant whichever DDL ran first won and
the other half of the system read its own tables wrongly — six fraud tables
existed only in a file nothing loaded, and 33 tests died on column mismatches.
The agent's shape wins on evidence: it is what the 42 property checks and the
live demo exercise, and its budget reservation is a compare-and-swap on exact
strings that NUMERIC would break silently.

**Does not solve:** The two audit chains still carry two hash algorithms in one
partitioned table, with nothing verifying one against the other (F5). Money as
TEXT needs a cast to sort by price. `src/api/decision/` and `audit/` remain
wired into tests rather than the running app.

## 30 · The Rappi bridge is a merchant-rail, not a rail

**Chose:** `src/rappi_bridge/` — a Python guard bridge that runs on the
credential machine only (the Rappi session token never leaves it), ports the
audited `@crafter/rappi-cli` endpoints natively, and is the only component
allowed to touch Rappi. It enforces by code: DRY_RUN default-on, a hardcoded
COP cap, clean-cart precondition with PUT-replace semantics, delivery-address
binding against the mandate, cent-exact drift rejection against the
kernel-approved amount, and single-flight idempotency (never re-clicking an
`uncertain` order). The kernel mints `capture-token+jwt` tokens (TTL ≤ 120 s)
binding `purchase_id + reservation_id + amount + cart_hash + dry_run`; the
bridge verifies them against the kernel JWKS and refuses to arm the checkout
without one — the human step-up is the key that unlocks the click. The click
IS the capture (Rappi vaults the card; no payment rail involved), completing
`pending_capture → captured`. Contract surface additive via
`aval/contracts/rappi-bridge.yaml`; `api.yaml` stays frozen. Full record:
`docs/decisions/0030-rappi-bridge-as-merchant-rail.md`.

**Rejected:** exposing the CLI's MCP/tools to the agent (raw `place_order` =
unguarded money path, S2); uploading the session to the cloud;
Playwright/DOM as primary (plan C if the undocumented API rotates);
client-side price tolerance (drift = reject + re-quote); re-clicking
`uncertain`.

**Why:** A live agentic run placed a real paid order and measured every
failure the design predicted: search quote COP 9,400 vs checkout COP 10,300
(store re-resolution + hidden service fee), search-coords vs account-active
address split-brain, server-side store minimums, and no CVC at checkout.
Kernel enforcement alone is not enough — the bridge is a second, independent
enforcement point on the machine that holds the money button.

**Does not solve:** undocumented-API drift (`app-version` hash rotation —
smoke test per session); Rappi's ToS §10 right to silently cancel under
fraud checks; purchases the owner makes outside the agent; human
reconciliation beyond `bridge.reconciled` events.

## 31 · One owner for project-wide IaC resources

**Chose:** In the shared `trytrust` GCP project, the dev state is the only
owner of enabled APIs and the `aval` Artifact Registry repository. The prod
state reads that repository, owns only `aval-prod-*` infrastructure and uses
independent production signing secrets. Full record:
`docs/decisions/0031-single-project-iac-ownership.md`.

**Rejected:** importing the same resources into both state files; sharing
signing keys across environments; migrating to a second GCP project during
this release.

**Why:** separate state files are ownership boundaries. Two states managing
one object can fight or destroy it, while sharing a private signing key lets a
dev compromise cross into production.

**Does not solve:** dev and prod still share project quotas, billing, API
enablement and part of the IAM blast radius. A separate production project is
the later hardening step.
