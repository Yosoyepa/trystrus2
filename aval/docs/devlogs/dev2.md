# Devlog — Dev 2 · Fraud, contracts, idempotency (kernel: decision core)

Mission: nothing out-of-mandate ever passes; the signed contracts verify for
real; every operation is exactly-once with cryptographically verifiable
evidence. Scope and day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md)
§3. Entry protocol: [`README.md`](README.md) — newest first, every PR.

---

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
