# Devlog — Dev 2 · Fraud, contracts, idempotency (kernel: decision core)

Mission: nothing out-of-mandate ever passes; the signed contracts verify for
real; every operation is exactly-once with cryptographically verifiable
evidence. Scope and day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md)
§3. Entry protocol: [`README.md`](README.md) — newest first, every PR.

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
