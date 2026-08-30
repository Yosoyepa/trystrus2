"""The configuration platform: people, agents, and versioned ontologies.

K1 is the property that makes this safe to expose: an ontology is unsigned
advice that shapes what the agent PROPOSES.  A mandate is signed law that
decides whether money MOVES.  Anyone may edit an agent here; nobody can widen a
spending limit by doing so.

Publishing a version is three writes in one transaction (E9, E12): append the
new version, move the pointer, record who changed it and why.  Nothing is
overwritten, so every past run can be replayed against the brain it actually had.
"""

from __future__ import annotations

import json

from . import audit
from .crypto.keys import load_or_create, public_jwk
from .ids import new_id, now_iso


# ── people ───────────────────────────────────────────────────────────────────
def add_person(
    conn, name: str, email: str | None = None, role: str = "member", person_id: str | None = None
) -> str:
    pid = person_id or new_id("per")
    conn.execute(
        "INSERT INTO people(id,name,email,role,created_at) VALUES(?,?,?,?,?)",
        (pid, name, email, role, now_iso()),
    )
    audit.append(conn, "person.created", {"person_id": pid, "name": name, "role": role}, actor=pid)
    return pid


def list_people(conn) -> list[dict]:
    return conn.execute("SELECT * FROM people ORDER BY created_at").fetchall()


# ── agents ───────────────────────────────────────────────────────────────────
def create_agent(
    conn,
    name: str,
    *,
    owner_id: str,
    approver_id: str | None = None,
    auditor_id: str | None = None,
    ontology: dict | None = None,
    model_cfg: dict | None = None,
    actor: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Creates the agent, its Ed25519 identity (C2) and version 1 of its brain."""
    aid = agent_id or new_id("agt")
    key = load_or_create(f"agent_{aid}")
    jwk = public_jwk(key, kid=aid)
    stamp = now_iso()
    conn.execute(
        "INSERT INTO agents(id,name,owner_id,approver_id,auditor_id,status,public_jwk,"
        "current_version,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,0,?,?)",
        (
            aid,
            name,
            owner_id,
            approver_id or owner_id,
            auditor_id or owner_id,
            json.dumps(jwk),
            stamp,
            stamp,
        ),
    )
    audit.append(
        conn,
        "agent.created",
        {
            "agent_id": aid,
            "name": name,
            "owner_id": owner_id,
            "approver_id": approver_id or owner_id,
            "public_jwk": jwk,
        },
        actor=actor or owner_id,
        agent_id=aid,
    )
    publish_version(
        conn,
        aid,
        ontology or {},
        model_cfg or {},
        changed_by=actor or owner_id,
        reason="initial version",
    )
    return aid


def publish_version(
    conn,
    agent_id: str,
    ontology: dict,
    model_cfg: dict | None = None,
    *,
    changed_by: str | None = None,
    reason: str = "",
) -> int:
    row = conn.execute("SELECT current_version FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such agent: {agent_id}")
    version = int(row["current_version"]) + 1
    stamp = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO agent_versions(agent_id,version,ontology,model_cfg,changed_by,"
            "reason,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                agent_id,
                version,
                json.dumps(ontology),
                json.dumps(model_cfg or {}),
                changed_by,
                reason,
                stamp,
            ),
        )
        conn.execute(
            "UPDATE agents SET current_version=?, updated_at=? WHERE id=?",
            (version, stamp, agent_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    audit.append(
        conn,
        "agent.version.published",
        {
            "agent_id": agent_id,
            "version": version,
            "reason": reason,
            "ontology_keys": sorted(ontology.keys()),
        },
        actor=changed_by,
        agent_id=agent_id,
    )
    return version


def get_agent(conn, agent_id: str) -> dict:
    row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such agent: {agent_id}")
    return row


def get_version(conn, agent_id: str, version: int | None = None) -> dict:
    if version is None:
        version = int(get_agent(conn, agent_id)["current_version"])
    row = conn.execute(
        "SELECT * FROM agent_versions WHERE agent_id=? AND version=?", (agent_id, version)
    ).fetchone()
    if row is None:
        raise KeyError(f"no version {version} for {agent_id}")
    return row


def list_agents(conn) -> list[dict]:
    return conn.execute(
        "SELECT a.*, o.name AS owner_name, p.name AS approver_name FROM agents a "
        "LEFT JOIN people o ON o.id=a.owner_id LEFT JOIN people p ON p.id=a.approver_id "
        "ORDER BY a.created_at"
    ).fetchall()


def history(conn, agent_id: str) -> list[dict]:
    return conn.execute(
        "SELECT v.*, p.name AS changed_by_name FROM agent_versions v "
        "LEFT JOIN people p ON p.id=v.changed_by WHERE v.agent_id=? "
        "ORDER BY v.version DESC",
        (agent_id,),
    ).fetchall()


def set_people(
    conn,
    agent_id: str,
    *,
    owner_id=None,
    approver_id=None,
    auditor_id=None,
    actor: str | None = None,
) -> None:
    current = get_agent(conn, agent_id)
    new = {
        "owner_id": owner_id or current["owner_id"],
        "approver_id": approver_id or current["approver_id"],
        "auditor_id": auditor_id or current["auditor_id"],
    }
    conn.execute(
        "UPDATE agents SET owner_id=?,approver_id=?,auditor_id=?,updated_at=? WHERE id=?",
        (new["owner_id"], new["approver_id"], new["auditor_id"], now_iso(), agent_id),
    )
    audit.append(
        conn, "agent.people.changed", {"agent_id": agent_id, **new}, actor=actor, agent_id=agent_id
    )


def set_status(conn, agent_id: str, status: str, actor: str | None = None) -> None:
    if status not in ("active", "paused", "retired"):
        raise ValueError(f"bad status: {status}")
    conn.execute(
        "UPDATE agents SET status=?, updated_at=? WHERE id=?", (status, now_iso(), agent_id)
    )
    audit.append(
        conn,
        "agent.status.changed",
        {"agent_id": agent_id, "status": status},
        actor=actor,
        agent_id=agent_id,
    )


def agent_private_key(agent_id: str):
    return load_or_create(f"agent_{agent_id}")
