"""PostgreSQL storage.

Dev and prod run the same engine on purpose: `compose.yaml` locally, Cloud SQL
in production, one Alembic migration for both.  A database that differs between
the two is how "works on my machine" becomes a demo failure.

The whole port fits behind `Conn`, a thin wrapper that translates `?` to `%s`
and hands back dict rows.  That is deliberate: it left ~145 existing
`conn.execute(...)` call sites in kernel, limits, registry, escalation and
watcher untouched, so the migration could not quietly change behaviour while
nobody was looking.  The tests are the proof — the same 28 properties pass
against Postgres that passed against SQLite.

Money stays TEXT rather than NUMERIC.  Amounts are fixed 2-decimal strings
everywhere they are signed (M7), and the budget reservation is a compare-and-
swap on the exact previous value (M2).  Exact string equality is precisely the
semantics that needs; NUMERIC would make `WHERE reserved_amount = '0.00'`
depend on how the server normalises `0.00` against `0`.
"""
from __future__ import annotations
import os
from typing import Any, Iterable, Sequence

import psycopg
from psycopg import sql as _sql  # noqa: F401  (kept for callers that compose SQL)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://trytrust:trytrust@localhost:5432/trytrust")

SCHEMA = """
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

-- ── mandates and instruments ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mandates (
  jti TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
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

-- ── merchant ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, category TEXT NOT NULL,
  title TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
  origin TEXT, destination TEXT, depart_date TEXT,
  description TEXT, active INTEGER NOT NULL DEFAULT 1
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
  nonce TEXT NOT NULL UNIQUE, intent TEXT NOT NULL, signature TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL
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
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY, scope TEXT NOT NULL, response TEXT,
  created_at TEXT NOT NULL
);

-- ── the agent's own runs ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
  agent_version INTEGER NOT NULL,             -- pinned at run start (E8, K3)
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
-- of them.
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

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION trytrust_append_only();
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
"""

TABLES = ["chat_messages", "outbox", "counters", "rate_buckets", "locks",  # locks kept in the drop list for old databases
          "checkpoints", "chains",
          "agent_runs", "escalations", "purchases", "purchase_intents",
          "idempotency_keys", "watches", "offers", "payment_instruments",
          "mandates", "agent_versions", "agents", "people", "audit_events"]


def _translate(sql: str) -> str:
    """`?` -> `%s`, and literal `%` -> `%%`, both outside string literals.

    Only applied when the caller passes parameters; a query with no parameters
    goes to the server byte for byte.
    """
    out: list[str] = []
    in_string = False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
            out.append(ch)
        elif in_string:
            out.append("%%" if ch == "%" else ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class Conn:
    """A psycopg connection that speaks the call sites' existing dialect."""

    def __init__(self, dsn: str | None = None, *, raw: psycopg.Connection | None = None):
        self._conn = raw or psycopg.connect(dsn or DSN, autocommit=True,
                                            row_factory=dict_row)
        self._owned = raw is None

    def execute(self, sql: str, args: Sequence[Any] | None = None):
        # The call sites use SQLite's exclusive-write idiom; Postgres takes the
        # lock at the row it touches, so a plain BEGIN plus the FOR UPDATE /
        # conditional UPDATE already in those queries is the same guarantee.
        stripped = sql.strip().upper()
        if stripped == "BEGIN IMMEDIATE":
            sql = "BEGIN"
        if not args:
            return self._conn.execute(sql)
        return self._conn.execute(_translate(sql), tuple(args))

    def executescript(self, script: str):
        return self._conn.execute(script)

    def cursor(self):
        return self._conn.cursor()

    def close(self) -> None:
        if self._owned:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_POOL: ConnectionPool | None = None


def pool(min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """Shared pool for workers that run many short transactions (relay, watcher)."""
    global _POOL
    if _POOL is None:
        _POOL = ConnectionPool(DSN, min_size=min_size, max_size=max_size,
                               kwargs={"autocommit": True, "row_factory": dict_row},
                               open=True)
    return _POOL


def connect(dsn: str | None = None) -> Conn:
    return Conn(dsn)


def init(dsn: str | None = None) -> Conn:
    conn = connect(dsn)
    conn.executescript(SCHEMA)
    return conn


def drop_all(conn: Conn) -> None:
    """Used by `cli reset` and the test harness. Triggers are dropped with the table."""
    conn.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
    conn.execute("DROP FUNCTION IF EXISTS trytrust_append_only() CASCADE")
    # Drop the migration stamp too, or `alembic upgrade head` afterwards would
    # believe the schema is current and leave the database empty.
    conn.execute("DROP TABLE IF EXISTS alembic_version")


def query(conn: Conn, sql: str, args: Iterable[Any] = ()) -> list[dict]:
    return conn.execute(sql, tuple(args)).fetchall()


def one(conn: Conn, sql: str, args: Iterable[Any] = ()) -> dict | None:
    return conn.execute(sql, tuple(args)).fetchone()
