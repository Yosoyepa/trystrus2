# Devlog — Dev 2 · Fraud, contracts, idempotency (kernel: decision core)

Mission: nothing out-of-mandate ever passes; the signed contracts verify for
real; every operation is exactly-once with cryptographically verifiable
evidence. Scope and day plan: [`../PLAN-PARALELO.md`](../PLAN-PARALELO.md)
§3. Entry protocol: [`README.md`](README.md) — newest first, every PR.

---

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
