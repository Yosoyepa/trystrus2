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
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql as _sql  # noqa: F401  (kept for callers that compose SQL)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def get_dsn() -> str:
    raw = (
        os.environ.get("AVAL_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql://aval:aval@localhost:5432/aval"
    )
    if "+asyncpg" in raw:
        raw = raw.replace("+asyncpg", "")
    return raw


DSN = get_dsn()

# `aval/contracts/fixtures/schema.sql` is the one description of the database
# in the repository — `compose.yaml` mounts that exact file into
# `/docker-entrypoint-initdb.d/`, so a container and this process reading the
# same bytes is what makes "works on my machine" not a demo failure. Resolved
# relative to this file rather than the CWD so it works identically from the
# CLI, from pytest (whatever directory collection started in), and from the
# Docker image — the Dockerfile copies `aval/` alongside `src/`, so the
# relative path from here (`src/agent/db.py` -> repo root -> `aval/...`)
# lands in the same place in every one of those environments.
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "aval" / "contracts" / "fixtures" / "schema.sql"


def _load_schema() -> str:
    try:
        return _SCHEMA_PATH.read_text()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"canonical schema not found at {_SCHEMA_PATH} — it ships with the repo "
            "under aval/contracts/fixtures/ and the Dockerfile copies aval/ into the "
            "image; if you moved either, fix the path above rather than re-inlining SQL"
        ) from exc


SCHEMA = _load_schema()

TABLES = [
    "chat_messages",
    "outbox",
    "counters",
    "rate_buckets",
    "locks",  # locks kept in the drop list for old databases
    "checkpoints",
    "chains",
    "agent_runs",
    "escalations",
    "purchases",
    "purchase_intents",
    "idempotency_keys",
    "watches",
    "offers",
    "payment_instruments",
    "mandates",
    "agent_versions",
    "agents",
    "people",
    "audit_events",
    # The rest of the canonical schema. `reset` means a clean slate, so it has
    # to drop every table the schema creates -- not only the ones this lane
    # writes. Leaving the identity, rail and fraud tables behind is how a demo
    # starts with a stale passkey credential or a yesterday's payment still in
    # it. The DROP is CASCADE, so listing order does not matter.
    "merchant_orders",
    "webauthn_challenges",
    "webauthn_credentials",
    "yuno_disputes",
    "yuno_idempotency",
    "yuno_payments",
    "yuno_payment_tokens",
    "yuno_setup_tokens",
    "velocity_counters",
    "baseline_hists",
    "baseline_metrics",
    "risk_lists",
    "risk_subjects",
    "webhook_archive",
]


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
        self._conn = raw or psycopg.connect(dsn or DSN, autocommit=True, row_factory=dict_row)
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
        _POOL = ConnectionPool(
            DSN,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
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
