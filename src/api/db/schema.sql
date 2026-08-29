-- Aval v1.1 seed schema. Monetary values are NUMERIC, never floating point.
CREATE TABLE IF NOT EXISTS mandates (
  id TEXT PRIMARY KEY, jti TEXT UNIQUE NOT NULL,
  user_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  claims JSONB NOT NULL, sd_jwt TEXT,
  reserved_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
  spent_total NUMERIC(12,2) NOT NULL DEFAULT 0,
  txn_count_period INT NOT NULL DEFAULT 0,
  parent_jti TEXT REFERENCES mandates(jti), version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS escalations (
  id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL, mandate_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', diff JSONB, timeout_at TIMESTAMPTZ NOT NULL,
  decision TEXT, approver TEXT, channel TEXT, receipt_sig TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS purchase_intents (
  jti TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, agent_id TEXT NOT NULL,
  intent_canonical JSONB NOT NULL, status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS purchases (
  id TEXT PRIMARY KEY, mandate_id TEXT NOT NULL, intent_jti TEXT NOT NULL,
  status TEXT NOT NULL, reason_code TEXT, reservation_id TEXT, receipt JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY, scope TEXT NOT NULL, response JSONB, derived_from TEXT,
  expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '45 days',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
  seq BIGSERIAL PRIMARY KEY, mandate_id TEXT NOT NULL, type TEXT NOT NULL,
  payload JSONB NOT NULL, prev_hash CHAR(64) NOT NULL, hash CHAR(64) NOT NULL,
  root_sig TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox (
  seq BIGSERIAL PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL, payload JSONB NOT NULL, relayed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS risk_subjects (
  subject_id UUID PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('human','agent')),
  agent_build TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS velocity_counters (
  mandate_id TEXT NOT NULL, counter TEXT NOT NULL, window TEXT NOT NULL,
  bucket_start TIMESTAMPTZ NOT NULL, val NUMERIC NOT NULL DEFAULT 0,
  PRIMARY KEY (mandate_id, counter, window, bucket_start)
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
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id UUID PRIMARY KEY, mandate_jti TEXT NOT NULL, node TEXT NOT NULL,
  state JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'running', escalation_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS payment_instruments (
  token_ref TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL,
  rail TEXT NOT NULL DEFAULT 'paypal', status TEXT NOT NULL DEFAULT 'active',
  fraudnet_session TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS webhook_archive (
  id BIGSERIAL PRIMARY KEY, source TEXT NOT NULL, headers JSONB NOT NULL,
  raw_body BYTEA NOT NULL, signature_valid BOOLEAN, resource_pulled BOOLEAN,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, category TEXT NOT NULL,
  title TEXT NOT NULL, amount NUMERIC(12,2) NOT NULL, currency TEXT NOT NULL,
  description TEXT, active BOOLEAN NOT NULL DEFAULT true
);
