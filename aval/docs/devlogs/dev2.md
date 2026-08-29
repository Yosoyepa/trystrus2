# Devlog — Dev 2 · Fraud, contracts, idempotency (kernel: decision core)

Mission: nothing out-of-mandate ever passes; the signed contracts verify for
real; every operation is exactly-once with cryptographically verifiable
evidence. Scope and day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md)
§3. Entry protocol: [`README.md`](README.md) — newest first, every PR.

---

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
