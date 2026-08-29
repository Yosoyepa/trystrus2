# Devlog — Dev 4 · Front & platform (web + bot + GCP infra)

Mission: humans, judges and auditors operate everything from clean
interfaces, and the system stays deployed and reproducible on GCP. Scope and
day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

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
