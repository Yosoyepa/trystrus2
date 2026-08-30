-- TryTrust / Aval — the one database schema.
--
-- This file is the single source of truth. `compose.yaml` mounts it into
-- `/docker-entrypoint-initdb.d/`, so it is what every local deployment gets;
-- `src/agent/db.py` loads it verbatim at runtime (see `_load_schema()` there)
-- so the Python process and the container agree by construction instead of
-- by discipline. Nothing else in the repository is allowed to declare a
-- second version of any table below — see `src/api/db/schema.sql`, which is
-- now just a pointer back here.
--
-- Section 1 holds every table either the agent lane (`src/agent`) or the api
-- lane (`src/api`, `src/merchant`, `src/yuno_sim`) shares. For a shared
-- table, the agent's original definition wins verbatim: money stays TEXT,
-- mandates key on `jti`, and every other lane column-renames or reformats to
-- match rather than the other way round. Reasons:
--
--   * Money stays TEXT rather than NUMERIC. Amounts are fixed 2-decimal
--     strings everywhere they are signed (M7), and `src/agent/kernel.py`'s
--     budget reservation is a compare-and-swap on the *exact previous
--     string value* (M2) — `WHERE reserved_amount = '0.00' AND ...`. Against
--     a NUMERIC column that CAS silently stops matching (`0` normalises
--     differently from `0.00`) and the result is a silent double-spend.
--   * `mandates` keys on `jti`, not a separately-minted `id`: the mandate's
--     own JWT id is the one identifier every lane already has to know, and
--     minting a second one only invites the two to drift.
--   * `mandate_jti` (not `mandate_id`) on the tables that hang off a
--     mandate, for the same reason.
--
-- A handful of columns exist on a shared table only because one lane's own
-- logic depends on them (idempotency TTL columns, the mandate's `sd_jwt` and
-- optimistic-lock `version`, an escalation's `resolved_at`, and so on). Those
-- are additive and nullable/defaulted, so they cost the other lane nothing —
-- true unions, not silent forks. They are commented as such below.
--
-- Section 2 holds the api-only tables (velocity/risk/step-up + webhook
-- archive) that used to live only in `src/api/db/schema.sql` and were
-- therefore never created in the composed stack. Section 3 holds the
-- identity/rail tables (passkeys, the merchant's own order record, the
-- simulated Yuno rail) that were already correct in this file.

-- ============================================================================
-- SECTION 1 — shared core (agent's definitions win; see header)
-- ============================================================================

-- ── configuration: editable, with an immutable record of every edit ──────────
CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT,
  role TEXT NOT NULL DEFAULT 'member',
  token_hash TEXT UNIQUE,              -- console credential, hashed (see auth.py)
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  owner_id TEXT REFERENCES people(id),
  approver_id TEXT REFERENCES people(id),
  auditor_id TEXT REFERENCES people(id),
  status TEXT NOT NULL DEFAULT 'active',      -- active|paused|retired
  public_jwk TEXT NOT NULL,
  current_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_versions (      -- append only (E9)
  agent_id TEXT NOT NULL REFERENCES agents(id),
  version INTEGER NOT NULL,
  ontology TEXT NOT NULL,                        -- JSON: domain knowledge (K1)
  model_cfg TEXT NOT NULL,
  changed_by TEXT REFERENCES people(id),
  reason TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (agent_id, version)
);

-- ── mandates and instruments ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mandates (
  jti TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  claims TEXT NOT NULL,
  token TEXT,                                     -- signed representation; NULL while [3]'s draft is unsigned
  reserved_amount TEXT NOT NULL DEFAULT '0.00',   -- written ONLY by verify (M3)
  spent_total TEXT NOT NULL DEFAULT '0.00',
  txn_count INTEGER NOT NULL DEFAULT 0,
  parent_jti TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  -- additive, [3]-only: the kernel's own SD-JWT bookkeeping. Never read or
  -- written by src/agent — kept nullable/defaulted so its inserts are unaffected.
  sd_jwt TEXT,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS payment_instruments (
  token_ref TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, rail TEXT NOT NULL DEFAULT 'yuno_sim',
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
  -- additive, [3]-only: soft-delete marker for a revoked instrument.
  deleted_at TEXT
);

-- ── merchant ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, category TEXT NOT NULL,
  title TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
  origin TEXT, destination TEXT, depart_date TEXT,
  description TEXT, active BOOLEAN NOT NULL DEFAULT TRUE
);

-- ── recurrent search: thresholds a human sets ───────────────────────────────
CREATE TABLE IF NOT EXISTS watches (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, mandate_jti TEXT NOT NULL,
  created_by TEXT REFERENCES people(id),
  query TEXT NOT NULL, threshold TEXT NOT NULL,
  interval_s INTEGER NOT NULL DEFAULT 300,
  autobuy INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  last_checked_at TEXT, last_seen_price TEXT, fired_at TEXT,
  created_at TEXT NOT NULL
);

-- ── decision and evidence ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS purchase_intents (
  jti TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, agent_id TEXT NOT NULL,
  nonce TEXT UNIQUE, intent TEXT, signature TEXT,
  status TEXT NOT NULL, created_at TEXT NOT NULL,
  -- additive, [2]-only: the api decision service's combined canonical intent.
  -- src/agent keeps its own `intent`/`signature`/`nonce` triple; nullable so
  -- neither lane's insert depends on the other's column.
  intent_canonical TEXT
);
CREATE TABLE IF NOT EXISTS purchases (
  id TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, intent_jti TEXT NOT NULL,
  status TEXT NOT NULL,
  reason_code TEXT, amount TEXT, reservation_id TEXT, receipt TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
  id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL, mandate_jti TEXT NOT NULL,
  run_id TEXT, approver_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  diff TEXT, timeout_at TEXT NOT NULL,
  decision TEXT, approver TEXT, channel TEXT, receipt_sig TEXT,
  created_at TEXT NOT NULL,
  -- additive, [3]-only: when the fail-closed sweep or a human resolved it.
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY, scope TEXT NOT NULL, response TEXT,
  created_at TEXT NOT NULL,
  -- additive, [2]-only: derived-key TTL bookkeeping (R-IDEM). src/agent's
  -- own rail mock (src/agent/mocks/rail.py) never sets these; both columns
  -- are nullable/defaulted so its INSERT is unaffected.
  derived_from TEXT,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '45 days'
);

-- ── the agent's own runs ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id TEXT PRIMARY KEY, agent_id TEXT,
  agent_version INTEGER,                      -- pinned at run start (E8, K3); [3]'s runs don't version an agent
  mandate_jti TEXT NOT NULL, session_id TEXT,
  node TEXT NOT NULL, state TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  escalation_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id BIGSERIAL PRIMARY KEY, session_id TEXT NOT NULL,
  role TEXT NOT NULL, text TEXT NOT NULL, run_id TEXT, created_at TEXT NOT NULL
);

-- ── the chain (E1-E5) ───────────────────────────────────────────────────────
-- Partitioned by chain_key so writers contend only within one mandate. A
-- single global chain made every event in the system queue behind one row:
-- to write entry N you must first read entry N-1. Marta's purchases no longer
-- wait behind Juan's; the checkpoints table restores one global proof over all
-- of them. [2]'s own ledger (src/api/audit) uses this same table under a
-- fixed chain_key of its own ('api-ledger') — a second partition in the same
-- log, verified by its own hash algorithm, never mixed with the agent's.
CREATE TABLE IF NOT EXISTS chains (
  chain_key TEXT PRIMARY KEY,
  head_hash TEXT NOT NULL,
  length BIGINT NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  seq BIGSERIAL PRIMARY KEY,
  chain_key TEXT NOT NULL,
  chain_seq BIGINT NOT NULL,
  event_id TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
  actor TEXT, agent_id TEXT, run_id TEXT, mandate_jti TEXT,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
  root_sig TEXT, created_at TEXT NOT NULL,
  UNIQUE (chain_key, chain_seq)
);
CREATE TABLE IF NOT EXISTS checkpoints (
  id BIGSERIAL PRIMARY KEY,
  root_hash TEXT NOT NULL, chain_heads TEXT NOT NULL,
  signature TEXT, chains_covered INTEGER NOT NULL, events_covered BIGINT NOT NULL,
  created_at TEXT NOT NULL
);

-- E1 is enforced by the database, not by convention: an append-only log
-- defended only by code review is not append-only.
CREATE OR REPLACE FUNCTION trytrust_append_only() RETURNS TRIGGER AS $fn$
BEGIN
  RAISE EXCEPTION '% is append-only (E1)', TG_TABLE_NAME;
END;
$fn$ LANGUAGE plpgsql;

-- `annotate_root` (src/api/audit's checkpoint annotation) is the one
-- sanctioned exception to E1: it fills in `root_sig` after the fact, and
-- only that column, on rows already committed. src/agent never updates a
-- committed audit_events row at all — its own checkpointing lives in the
-- separate `checkpoints` table — so this is strictly more permissive than
-- the blanket rule only for a case the agent lane never exercises.
CREATE OR REPLACE FUNCTION trytrust_audit_events_root_sig_only() RETURNS TRIGGER AS $fn$
BEGIN
  -- Compare the whole row rather than a list of columns: a column added later
  -- is then protected by default instead of silently becoming mutable, and the
  -- attribution columns (actor, agent_id, run_id, mandate_jti) stay prevented
  -- rather than merely detectable. They are inside the hash, so mutating one
  -- would be caught by a chain replay -- but E1 says the database refuses, not
  -- that the auditor notices afterwards.
  IF (to_jsonb(NEW) - 'root_sig') IS DISTINCT FROM (to_jsonb(OLD) - 'root_sig') THEN
    RAISE EXCEPTION 'audit_events is append-only except root_sig (E1)';
  END IF;
  -- Write-once: a signature that can be quietly replaced is not evidence.
  IF OLD.root_sig IS NOT NULL AND NEW.root_sig IS DISTINCT FROM OLD.root_sig THEN
    RAISE EXCEPTION 'audit_events.root_sig is write-once (E1)';
  END IF;
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION trytrust_audit_events_root_sig_only();
DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events;
CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION trytrust_append_only();
DROP TRIGGER IF EXISTS agent_versions_no_update ON agent_versions;
CREATE TRIGGER agent_versions_no_update BEFORE UPDATE ON agent_versions
  FOR EACH ROW EXECUTE FUNCTION trytrust_append_only();

CREATE TABLE IF NOT EXISTS outbox (
  seq BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL, aggregate_id TEXT NOT NULL, payload TEXT NOT NULL,
  relayed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  created_at TEXT NOT NULL
);

-- ── guardrails ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rate_buckets (
  key TEXT PRIMARY KEY, tokens DOUBLE PRECISION NOT NULL, updated_at TEXT NOT NULL
);
-- (the `locks` table is retired: single-flight uses Postgres advisory locks,
--  which need no TTL because the lock dies with the session — a crashed holder
--  releases immediately instead of wedging the system until a timeout expires)
CREATE TABLE IF NOT EXISTS counters (
  key TEXT NOT NULL, window_key TEXT NOT NULL, value DOUBLE PRECISION NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL, PRIMARY KEY (key, window_key)
);

CREATE INDEX IF NOT EXISTS ix_audit_chain ON audit_events(chain_key, chain_seq);
CREATE INDEX IF NOT EXISTS ix_audit_mandate ON audit_events(mandate_jti);
CREATE INDEX IF NOT EXISTS ix_audit_agent ON audit_events(agent_id);
CREATE INDEX IF NOT EXISTS ix_audit_run ON audit_events(run_id);
CREATE INDEX IF NOT EXISTS ix_runs_session ON agent_runs(session_id);
CREATE INDEX IF NOT EXISTS ix_outbox_undelivered ON outbox(relayed_at, seq);
CREATE INDEX IF NOT EXISTS ix_watches_due ON watches(status, last_checked_at);
CREATE INDEX IF NOT EXISTS ix_purchases_mandate ON purchases(mandate_jti, status);

-- ============================================================================
-- SECTION 2 — api-only: velocity, risk, step-up (decision 0021/0024 family)
-- ============================================================================
-- These belong to [2] alone: no other lane reads or writes them. They were
-- previously declared only in src/api/db/schema.sql, which nothing in the
-- composed stack loaded — so in a real `docker compose up` they never
-- existed and every write to them (repository_postgres.py's velocity store,
-- in particular) failed outright. They live here now, so the deployed
-- database actually has them.
CREATE TABLE IF NOT EXISTS risk_subjects (
  subject_id UUID PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('human','agent')),
  agent_build TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS velocity_counters (
  mandate_id TEXT NOT NULL, counter TEXT NOT NULL, "window" TEXT NOT NULL,
  bucket_start TIMESTAMPTZ NOT NULL, val NUMERIC NOT NULL DEFAULT 0,
  PRIMARY KEY (mandate_id, counter, "window", bucket_start)
);
CREATE TABLE IF NOT EXISTS baseline_metrics (
  subject_id UUID NOT NULL REFERENCES risk_subjects, metric TEXT NOT NULL,
  ewma DOUBLE PRECISION NOT NULL, ewma_var DOUBLE PRECISION NOT NULL,
  lambda DOUBLE PRECISION NOT NULL DEFAULT 0.15, n_obs BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (subject_id, metric)
);
CREATE TABLE IF NOT EXISTS baseline_hists (
  subject_id UUID NOT NULL REFERENCES risk_subjects, dim TEXT NOT NULL,
  value_h TEXT NOT NULL, count BIGINT NOT NULL DEFAULT 1,
  last_seen TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (subject_id, dim, value_h)
);
CREATE TABLE IF NOT EXISTS risk_lists (
  subject_type TEXT NOT NULL, subject_id_h TEXT NOT NULL,
  list TEXT NOT NULL CHECK (list IN ('block','allow')), reason TEXT, expires_at TIMESTAMPTZ,
  PRIMARY KEY (subject_type, subject_id_h, list)
);
CREATE TABLE IF NOT EXISTS webhook_archive (
  id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, headers JSONB NOT NULL,
  raw_body BYTEA NOT NULL, signature_valid BOOLEAN, resource_pulled BOOLEAN,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- SECTION 3 — identity/rail: passkeys, merchant checkout, the simulated rail
-- ============================================================================
CREATE TABLE IF NOT EXISTS webauthn_credentials (
  credential_id TEXT PRIMARY KEY,              -- base64url, from the authenticator
  user_id TEXT NOT NULL,
  public_key BYTEA NOT NULL,                   -- COSE key
  sign_count BIGINT NOT NULL DEFAULT 0,        -- monotonic; a decrease means a clone
  transports TEXT[],
  aaguid TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS webauthn_credentials_user_idx
  ON webauthn_credentials (user_id);

CREATE TABLE IF NOT EXISTS webauthn_challenges (
  challenge TEXT NOT NULL,
  user_id TEXT NOT NULL,
  mandate_id TEXT,                             -- set when the challenge is a mandate hash
  purpose TEXT NOT NULL,                       -- register|activate|revoke
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ,                     -- single use; NULL until spent
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (challenge, purpose)
);
-- A mandate's canonical hash intentionally stays identical for activation
-- and revocation. Store purpose in its primary key so a fresh revocation
-- ceremony cannot collide with an already-consumed activation challenge.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'webauthn_challenges_pkey'
      AND conrelid = 'webauthn_challenges'::regclass
      AND pg_get_constraintdef(oid) NOT LIKE 'PRIMARY KEY (challenge, purpose)%'
  ) THEN
    ALTER TABLE webauthn_challenges DROP CONSTRAINT webauthn_challenges_pkey;
    ALTER TABLE webauthn_challenges
      ADD CONSTRAINT webauthn_challenges_pkey PRIMARY KEY (challenge, purpose);
  END IF;
END $$;

-- Merchant-owned checkout record. A Checkout JWT is persisted before the
-- agent signs its hash, so a charge can verify the exact cart rather than
-- reconstructing one after the fact.
CREATE TABLE IF NOT EXISTS merchant_orders (
  id TEXT PRIMARY KEY,
  offer_id TEXT NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  currency TEXT NOT NULL,
  checkout_jwt TEXT UNIQUE NOT NULL,
  checkout_hash TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'quoted',
  purchase_id TEXT,
  receipt JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- A purchase has one cart and a cart has one captured purchase. The partial
-- form leaves unlimited quoted carts with no purchase id.
CREATE UNIQUE INDEX IF NOT EXISTS merchant_orders_purchase_id_unique
  ON merchant_orders (purchase_id) WHERE purchase_id IS NOT NULL;

-- ── the simulated Yuno-style AP2 orchestrator (decision 0024) ──────────────
-- Separate deployable, separate concern: these are the rail's own books, not
-- ours. It stores no PAN and no card data — only opaque vaulted tokens.
CREATE TABLE IF NOT EXISTS yuno_setup_tokens (
  setup_token_id TEXT PRIMARY KEY,
  mandate_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending|approved|exchanged|expired
  approve_url TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS yuno_payment_tokens (
  token_id TEXT PRIMARY KEY,
  setup_token_id TEXT,
  mandate_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',    -- active|deleted
  instrument_label TEXT,                    -- e.g. "VISA ****4242" — never a PAN
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS yuno_payments (
  payment_id TEXT PRIMARY KEY,
  token_id TEXT NOT NULL,
  mandate_jti TEXT NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  currency TEXT NOT NULL,
  status TEXT NOT NULL,                     -- captured|refused
  reason_code TEXT,                         -- set when refused
  checkout_hash TEXT,                       -- AP2 binding actually verified
  intent_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Same key, same answer, no second charge — enforced by the PK, not by a check.
CREATE TABLE IF NOT EXISTS yuno_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL,
  response JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS yuno_disputes (
  dispute_id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT 'UNAUTHORISED',
  status TEXT NOT NULL DEFAULT 'open',      -- open|resolved
  outcome TEXT,                             -- BUYER_FAVOR|SELLER_FAVOR
  evidence JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);
