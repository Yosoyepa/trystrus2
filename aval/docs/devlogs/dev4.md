# Devlog — Dev 4 · Front & platform (web + bot + GCP infra)

Mission: humans, judges and auditors operate everything from clean
interfaces, and the system stays deployed and reproducible on GCP. Scope and
day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 — Full Docker / Podman Containerization & Compose Orchestration
- **Why:** Enable one-command reproducible local and demo deployment across all 5 Aval microservices.
- **Implemented:**
  - Multi-stage `Dockerfile` (Python 3.13-slim + `uv`) for backend deployables (`kernel` :8001, `yuno_sim` :8002, `merchant` :8003).
  - Multi-stage `web/Dockerfile` (Node 20 Alpine builder -> Nginx 1.27 Alpine) serving the React SPA on port 3000.
  - `web/nginx.conf` reverse proxy routing `/api/` -> `kernel:8001/`, `/yuno/` -> `yuno_sim:8002/`, and `/merchant/` -> `merchant:8003/` with SPA client-side fallback.
  - `compose.yaml` and `docker-compose.yml` wiring all 5 services: `db` (Postgres 16 + DDL fixtures), `kernel`, `yuno_sim`, `merchant`, and `web` with inter-service dependencies, volume persistence, and healthchecks.
  - `scripts/start-all.sh`: One-stop launcher supporting containerized compose mode (`--compose`), local host background runner with PID tracking (`--local`), and clean teardown (`--stop`).
  - `scripts/smoke-test.sh`: Automated healthcheck suite verifying all direct endpoints and reverse proxy routes return HTTP 200 OK.
- **Verification:** All 5 containers build cleanly, reach healthy status, and pass all smoke tests. `uv run pytest` reports 307 passing tests. `scripts/docs-guard.sh` passes.

## 2026-08-29 — React + TypeScript Single Page Application (SPA) Delivery in web/
- **Why:** Full interactive and visual operation for judges, auditors, buyers, and merchants.
- **Implemented:**
  - Vite + React 18 + TypeScript + Tailwind CSS + Lucide icons scaffold in `web/` with API proxy rules for `/api` (:8001), `/yuno` (:8002), and `/merchant` (:8003).
  - **Auditor / Judge Control Tower (`AuditorTower.tsx`)**:
    - Real-time Hash-Chained Ledger table with payload inspection (RFC 8785 / JCS) & KMS root signatures.
    - Live Chain Verifier (`verify_all()`) with instant pass/fail walk.
    - Tamper Mutation Injector sandbox with real-time fail-closed Red Alert, cryptographic hash diff, and genesis reset.
    - Escalations Queue with 120s countdown timer, proposal diff, WebAuthn biometric approval, and sticky mini-mandate derivation.
    - Telemetry Dashboard with active PG advisory locks, rate limit token buckets, spend counters, and outbox relay queue depth.
    - Cryptographic Evidence Pack Viewer for dispute non-repudiation.
  - **Buyer Console (`BuyerConsole.tsx`)**:
    - Passkey WebAuthn ceremony simulator (Touch ID / Face ID / FIDO2).
    - SD-JWT Mandate Creator with JsonLogic rules and selective disclosures (RFC 9901).
    - Mandate Card with decoded claim inspector, active budget progress bar, and CAS meters.
    - AI Agent Interactive Chat with visual graph state machine (`[perceive] -> [search] -> [propose] -> [gate] -> [receipt]`).
    - Dual Kill Switch with live latency stopwatch (<2.0s SLA) executing mandate revocation and rail token deletion.
  - **Merchant Console & Rail (`MerchantConsole.tsx`)**:
    - VuelaYa Flight Catalog with dynamic price editor (`POST /admin/offers/{id}/price`).
    - 7-Step Cryptographic Verification Pipeline visualizer with real-time step inspection.
    - Yuno AP2 Simulated Rail with vaulted token registry, capture ledger, and dispute adjudication engine.
  - **1-Click Interactive Demo Runner (`DemoRunner.tsx`)**:
    - End-to-end automated and step-by-step runner for all 10 judging scenarios with live status badges and evidence envelopes.
- **Contracts touched:** none (consumed `api.yaml` and `schemas.md`).
- **Verification:** `npm run build` succeeds with 0 TypeScript/build errors.

## 2026-08-29 — workstream opened at M0 freeze
- **Why:** contracts v1.0 are frozen; this log exists so nobody re-solves what Dev 4 already solved.
- **Decision:** none yet. Starting points:
  [`../../DECISIONS.md`](../../DECISIONS.md) #11 (Cloud Run + Cloud SQL),
  #12 (frontend as its own service), #13 (Telegram HIL). Types are GENERATED
  from [`../../contracts/api.yaml`](../../contracts/api.yaml) — never
  hand-written.
- **Contracts touched:** none.
- **Tests I own:** T15 (smoke against GCP), T16 (bootstrap idempotency).
- **Open questions:** none.
