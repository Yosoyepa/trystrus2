"""Safety tests for recovery of the local demo seed."""

from __future__ import annotations

import sqlite3

from src.agent import db, seed
from src.agent.seed import needs_demo_seed
from src.api import deps


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("CREATE TABLE mandates (jti TEXT PRIMARY KEY, status TEXT NOT NULL)")
    return conn


def test_empty_database_needs_demo_seed() -> None:
    assert needs_demo_seed(_conn()) is True


def test_partial_rappi_demo_seed_needs_recovery() -> None:
    conn = _conn()
    conn.execute("INSERT INTO agents(id, name) VALUES (?, ?)", ("agt_rappi", "rappi_comprador"))

    assert needs_demo_seed(conn) is True


def test_user_agent_without_mandate_is_not_automatically_authorized() -> None:
    conn = _conn()
    conn.execute("INSERT INTO agents(id, name) VALUES (?, ?)", ("agt_user", "user_agent"))

    assert needs_demo_seed(conn) is False


def test_existing_revoked_mandate_is_not_reseeded() -> None:
    conn = _conn()
    conn.execute("INSERT INTO agents(id, name) VALUES (?, ?)", ("agt_rappi", "rappi_comprador"))
    conn.execute("INSERT INTO mandates(jti, status) VALUES (?, ?)", ("mdt_revoked", "revoked"))

    assert needs_demo_seed(conn) is False


def test_agent_connection_repairs_only_a_partial_demo_seed(monkeypatch) -> None:
    conn = _conn()
    conn.execute("INSERT INTO agents(id, name) VALUES (?, ?)", ("agt_rappi", "rappi_comprador"))
    seeded: list[sqlite3.Connection] = []
    monkeypatch.setattr(db, "init", lambda: conn)
    monkeypatch.setattr(seed, "seed_all", lambda received: seeded.append(received))
    deps.reset()

    try:
        assert deps.agent_conn() is conn
        assert seeded == [conn]
    finally:
        deps.reset()


def test_agent_connection_does_not_replace_revoked_authority(monkeypatch) -> None:
    conn = _conn()
    conn.execute("INSERT INTO agents(id, name) VALUES (?, ?)", ("agt_rappi", "rappi_comprador"))
    conn.execute("INSERT INTO mandates(jti, status) VALUES (?, ?)", ("mdt_revoked", "revoked"))
    seeded: list[sqlite3.Connection] = []
    monkeypatch.setattr(db, "init", lambda: conn)
    monkeypatch.setattr(seed, "seed_all", lambda received: seeded.append(received))
    deps.reset()

    try:
        assert deps.agent_conn() is conn
        assert seeded == []
    finally:
        deps.reset()
