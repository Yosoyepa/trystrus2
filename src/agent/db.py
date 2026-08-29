"""SQLite storage.

Local demo runs on SQLite so a fresh clone works with no external service
(README goal).  The DDL mirrors `contracts/schemas.md` section 6 so the move to
Cloud SQL Postgres (decision #11) is a dialect change, not a redesign.

E1 is enforced by the database itself: triggers make `audit_events` reject
UPDATE and DELETE.  An append-only log defended only by convention is not
append-only.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── configuration: editable, with an immutable record of every edit ──────────
CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT,
  role TEXT NOT NULL DEFAULT 'member', created_at TEXT NOT NULL
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

-- ── mandates and instruments (kernel identity; mocked here) ──────────────────
CREATE TABLE IF NOT EXISTS mandates (
  jti TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',      -- active|suspended|revoked|expired|exhausted
  claims TEXT NOT NULL, token TEXT NOT NULL,
  reserved_amount TEXT NOT NULL DEFAULT '0.00',   -- written ONLY by verify (M3)
  spent_total TEXT NOT NULL DEFAULT '0.00',
  txn_count INTEGER NOT NULL DEFAULT 0,
  parent_jti TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_instruments (
  token_ref TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, rail TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL
);

-- ── merchant (mock VuelaYa) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, category TEXT NOT NULL,
  title TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
  origin TEXT, destination TEXT, depart_date TEXT,
  description TEXT, active INTEGER NOT NULL DEFAULT 1
);

-- ── recurrent search: thresholds a human sets ────────────────────────────────
CREATE TABLE IF NOT EXISTS watches (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, mandate_jti TEXT NOT NULL,
  created_by TEXT REFERENCES people(id),
  query TEXT NOT NULL,                        -- JSON search criteria
  threshold TEXT NOT NULL,                    -- JsonLogic over {offer, now}
  interval_s INTEGER NOT NULL DEFAULT 300,
  autobuy INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',      -- active|paused|fired|cancelled
  last_checked_at TEXT, last_seen_price TEXT, fired_at TEXT,
  created_at TEXT NOT NULL
);

-- ── decision and evidence ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS purchase_intents (
  jti TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, agent_id TEXT NOT NULL,
  nonce TEXT NOT NULL UNIQUE, intent TEXT NOT NULL, signature TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchases (
  id TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, intent_jti TEXT NOT NULL,
  status TEXT NOT NULL,   -- pending|awaiting_escalation|charging|captured|rejected|compensated
  reason_code TEXT, amount TEXT, reservation_id TEXT, receipt TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
  id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL, mandate_jti TEXT NOT NULL,
  run_id TEXT, approver_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',     -- pending|resolved|expired
  diff TEXT, timeout_at TEXT NOT NULL,
  decision TEXT, approver TEXT, channel TEXT, receipt_sig TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY, scope TEXT NOT NULL, response TEXT,
  created_at TEXT NOT NULL
);

-- ── the agent's own runs ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
  agent_version INTEGER NOT NULL,             -- pinned at run start (E8, K3)
  mandate_jti TEXT NOT NULL, session_id TEXT,
  node TEXT NOT NULL, state TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',     -- running|awaiting_human|done|denied|failed
  escalation_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
  role TEXT NOT NULL, text TEXT NOT NULL, run_id TEXT, created_at TEXT NOT NULL
);

-- ── the chain (E1-E5) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
  actor TEXT, agent_id TEXT, run_id TEXT, mandate_jti TEXT,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
  root_sig TEXT, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit_events is append-only (E1)');
END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit_events is append-only (E1)');
END;
CREATE TRIGGER IF NOT EXISTS agent_versions_no_update
BEFORE UPDATE ON agent_versions BEGIN
  SELECT RAISE(ABORT, 'agent_versions is append-only (E9)');
END;

CREATE TABLE IF NOT EXISTS outbox (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL, aggregate_id TEXT NOT NULL, payload TEXT NOT NULL,
  relayed_at TEXT, created_at TEXT NOT NULL
);

-- ── guardrails: rate limits, single-flight locks, spend counters ────────────
CREATE TABLE IF NOT EXISTS rate_buckets (
  key TEXT PRIMARY KEY, tokens REAL NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locks (
  name TEXT PRIMARY KEY, holder TEXT NOT NULL,
  acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (
  key TEXT NOT NULL, window TEXT NOT NULL, value REAL NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL, PRIMARY KEY (key, window)
);

CREATE INDEX IF NOT EXISTS ix_audit_mandate ON audit_events(mandate_jti);
CREATE INDEX IF NOT EXISTS ix_audit_agent ON audit_events(agent_id);
CREATE INDEX IF NOT EXISTS ix_audit_run ON audit_events(run_id);
CREATE INDEX IF NOT EXISTS ix_runs_session ON agent_runs(session_id);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, isolation_level=None, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(path: Path | None = None) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn


def query(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, tuple(args)).fetchall()


def one(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(args)).fetchone()
