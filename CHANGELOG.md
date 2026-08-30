# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.0.1-alpha] - 2026-08-30

### Summary
Initial unified alpha pre-release of **Aval (TryTrust)**: a trust and cryptographic verification layer for purchases initiated by AI agents (NextWave Hackathon 2026, Yuno × Nauta).

This release integrates the core capabilities developed in parallel across Dev 1, Dev 2, and Dev 3:

### Added

#### Dev 1 — Agentic Orchestration & Guardrails (`src/agent/`)
- **Framework-Free Explicit Graph**: ~150-line state machine (`perceive` → `search` → `propose` → `gate` → `await_human` → `receipt` → `done`) strictly confining LLMs to proposal generation without execution authority.
- **Background Watcher Daemon**: Cron/Cloud Scheduler recurring search engine with JSONLogic threshold evaluations and atomic job claiming via PostgreSQL `SKIP LOCKED`.
- **4-Tier Containment & Guardrails**: Structural money tool refusal, persistent token bucket rate limiters, windowed spend counters, and single-flight PostgreSQL advisory locks.
- **Partitioned Hash Chains & Merkle Roots**: Tamper-evident append-only ledger partitioned per mandate with global signed root checkpoints.
- **MCP Integration Layer**: Real-world adapters for `VuelaYa` (flights) and `Mami` (groceries), enforcing strict `read` / `submit` separation and rejecting unsafe `pay` tools.
- **Security & Privacy**: PII scrubber masking PAN, CVV, and emails; in-process HTTP egress allowlist; RBAC console auth with SHA-256 hashed bearer tokens.
- **Testing & Verification**: 42 property-based integration tests in `src/agent/tests.py`.

#### Dev 2 — Deterministic Policy Gate, Evidence & Outbox (`src/api/`)
- **Deterministic Policy Gate (`domain/`)**: Pure rules engine enforcing R-PRICE (exact cent matching), R-BURST (velocity limits & cooldowns), R-STEPUP (L3/L3+ risk ratios & TTLs), and the Gold Rule arbitration.
- **Decision & Verify Saga (`decision/`)**: Atomic conditional CAS budget reservation against PostgreSQL, idempotent request coordination with canonical request fingerprinting (45-day retention), and compensation rollback.
- **Cryptographic Audit Ledger (`audit/`)**: Pure hash-chain validation algebra (RT-9), append-only PostgreSQL storage with tail-lock serialization, Google Cloud KMS `EC_SIGN_ED25519` signers, and write-once immutable Google Cloud Storage witness adapters.
- **Transactional Outbox Relay (`events/`)**: Background poller draining events via `SELECT ... FOR UPDATE SKIP LOCKED` and delivering Ed25519-signed HTTP webhooks.
- **R-EVIDENCE Pack Generator (`evidence/`)**: Aggregator bundling mandate claims, intent, decision snapshot, receipt, and ledger verification into a tamper-proof envelope.
- **Testing & Verification**: 128 unit and property tests (`src/api/tests/`) including Hypothesis derandomized evaluations.

#### Dev 3 — Identity, SD-JWT, Merchant & Yuno AP2 Simulator (`src/trustlib/`, `src/merchant/`, `src/yuno_sim/`)
- **SD-JWT RFC 9901 & AP2 Mandates (`trustlib/`)**: Selective disclosure JWT issuance with SHA-256 salted disclosures, Key Binding JWT proof-of-possession, and ES256 Checkout JWT cart locking.
- **Passkey Ceremony & WebAuthn (`src/api/services/passkey.py`)**: Purpose-bound biometric ceremonies (`REGISTER`, `ACTIVATE`, `REVOKE`) signing the canonical mandate hash with monotonic signature counter validation.
- **VuelaYa Merchant Service (`src/merchant/`)**: Flight catalog with dynamic price overrides, FastMCP server with prompt injection isolation (`<merchant-data>`), and fail-closed 7-step cryptographic checkout charge pipeline.
- **Yuno-Style Simulated AP2 Rail (`src/yuno_sim/`)**: Simulated payment orchestrator providing setup token vaulting, idempotent captures, dispute adjudication, and independent rail-side mandate liveness verification (`GET /mandates/by-jti/{jti}`).
- **OpenAPI v1.1.0 Contracts**: Synchronized `aval/contracts/api.yaml` and frozen test fixtures.
- **Testing & Verification**: 234 test cases covering SD-JWT, state machine transitions, rail idempotency, and passkey ceremonies.

#### Architecture & Documentation
- Consolidated decision register (Decisions 0001 through 0028) in `aval/DECISIONS.md`.
- Append-only devlogs under `aval/docs/devlogs/`.
- CI documentation enforcement via `scripts/docs-guard.sh`.
