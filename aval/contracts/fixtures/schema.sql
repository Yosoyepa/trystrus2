-- Full seed DDL — schemas.md §6, plus decisions 0021 and 0024.
--
-- This file exists so a local `docker compose up` gives every workstream the
-- whole schema, including tables it does not own. It is NOT the migration
-- path: each dev owns an alembic migration for their own tables
-- (PLAN-PARALELO §6.4). Ownership is marked per block.

-- ==========================================================================
-- [3] identity — mandates, escalations
-- ==========================================================================
CREATE TABLE IF NOT EXISTS mandates (
  id TEXT PRIMARY KEY,
  jti TEXT UNIQUE NOT NULL,
  user_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',        -- draft|active|suspended|revoked|expired|exhausted
  claims JSONB NOT NULL,                       -- MandateClaims (canonical, signed)
  sd_jwt TEXT,
  reserved_amount NUMERIC(12,2) NOT NULL DEFAULT 0,  -- written ONLY by verify [2]
  spent_total NUMERIC(12,2) NOT NULL DEFAULT 0,      -- written ONLY by verify [2]
  txn_count_period INT NOT NULL DEFAULT 0,           -- written ONLY by verify [2]
  parent_jti TEXT REFERENCES mandates(jti),    -- sticky mini-mandates
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mandates_user_idx ON mandates (user_id);
CREATE INDEX IF NOT EXISTS mandates_status_idx ON mandates (status);

CREATE TABLE IF NOT EXISTS escalations (
  id TEXT PRIMARY KEY,
  purchase_id TEXT NOT NULL,
  mandate_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending|resolved|expired
  diff JSONB,
  timeout_at TIMESTAMPTZ NOT NULL,
  decision TEXT,
  approver TEXT,
  channel TEXT,
  receipt_sig TEXT,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The fail-closed sweep reads this: pending escalations past their deadline.
CREATE INDEX IF NOT EXISTS escalations_pending_idx
  ON escalations (status, timeout_at) WHERE status = 'pending';

-- [3] passkeys — decision 0021 (absent from the original §6 DDL)
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

-- ==========================================================================
-- [2] decision + evidence
-- ==========================================================================
CREATE TABLE IF NOT EXISTS purchase_intents (
  jti TEXT PRIMARY KEY,
  mandate_jti TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  intent_canonical JSONB NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchases (
  id TEXT PRIMARY KEY,
  mandate_id TEXT NOT NULL,
  intent_jti TEXT NOT NULL,
  status TEXT NOT NULL,   -- pending_verification|awaiting_escalation|charging|captured|rejected|compensated
  reason_code TEXT,
  reservation_id TEXT,
  receipt JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  response JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
  seq BIGSERIAL PRIMARY KEY,
  mandate_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload JSONB NOT NULL,
  prev_hash CHAR(64) NOT NULL,
  hash CHAR(64) NOT NULL,
  root_sig TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [2] owns the DDL; [3] appends through trustlib.events.emit_event (decision 0022)
CREATE TABLE IF NOT EXISTS outbox (
  seq BIGSERIAL PRIMARY KEY,
  event_id TEXT UNIQUE NOT NULL,
  type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  relayed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The relay's claim query: FOR UPDATE SKIP LOCKED over unrelayed rows.
CREATE INDEX IF NOT EXISTS outbox_unrelayed_idx
  ON outbox (seq) WHERE relayed_at IS NULL;

-- ==========================================================================
-- [1] agent runs — own-graph checkpointing (decision #16)
-- ==========================================================================
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id UUID PRIMARY KEY,
  mandate_jti TEXT NOT NULL,
  node TEXT NOT NULL,        -- perceive|search|propose|gate|await_human|pay|receipt|done|denied
  state JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',  -- running|awaiting_human|done|denied|failed
  escalation_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ==========================================================================
-- [3] commerce
-- ==========================================================================
CREATE TABLE IF NOT EXISTS payment_instruments (
  token_ref TEXT PRIMARY KEY,
  mandate_jti TEXT NOT NULL,
  rail TEXT NOT NULL DEFAULT 'yuno_sim',   -- was 'paypal' before decision 0024
  status TEXT NOT NULL DEFAULT 'active',   -- active|deleted
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS payment_instruments_mandate_idx
  ON payment_instruments (mandate_jti);

CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY,
  merchant_id TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  currency TEXT NOT NULL,
  origin TEXT,
  destination TEXT,
  travel_date DATE,
  description TEXT,
  active BOOLEAN NOT NULL DEFAULT true
);
-- Existing local development databases predate v1.1; keep bootstrap
-- idempotent instead of requiring everyone to destroy their demo state.
ALTER TABLE offers ADD COLUMN IF NOT EXISTS origin TEXT;
ALTER TABLE offers ADD COLUMN IF NOT EXISTS destination TEXT;
ALTER TABLE offers ADD COLUMN IF NOT EXISTS travel_date DATE;

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

-- ==========================================================================
-- [3] the simulated Yuno-style AP2 orchestrator (decision 0024)
-- ==========================================================================
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
