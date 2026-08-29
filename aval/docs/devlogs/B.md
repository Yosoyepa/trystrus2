# Devlog — Workstream B · Decision & evidence (kernel)

Mission: nothing out-of-mandate ever passes, and every decision leaves
cryptographically verifiable evidence. Scope, components and day plan:
[`../PLAN-PARALELO.md`](../PLAN-PARALELO.md) §3. Entry protocol:
[`README.md`](README.md) — newest first, every PR.

---

## 2026-08-29 — workstream opened at M0 freeze
- **Why:** contracts v1.0 are frozen; this log exists so nobody re-solves what B already solved.
- **Decision:** none yet. Starting points: [`../../DECISIONS.md`](../../DECISIONS.md) #1 (no model in enforcement), #5 (JSON Logic), #7 (hash chain), #10 (outbox).
- **Contracts touched:** none.
- **Tests I own:** T1 (invariant, property-based), T4, T5 (race), T6 (TOCTOU), T9 (hash chain), T10 (JsonLogic), T13 (demo-as-code).
- **Open questions:** none.
