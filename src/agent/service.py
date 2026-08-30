"""One module for the API lane to import.

`src/api/` should not need to know that a run is checkpointed in `agent_runs`,
that escalations resume through the gate, or that the outbox needs draining.
Every function here takes a connection and returns plain dicts, ready to be
JSON-encoded by whatever web framework sits in front.

Authentication happens HERE, not in the layers below, because this is the edge
where untrusted input arrives. A caller without a token can read; changing
anything needs one (see auth.py).
"""
from __future__ import annotations
import json
from typing import Any

from . import audit, auth, chat, db, escalation, graph, limits, memory
from . import mandate as mandate_mod
from . import registry, relay, watcher
from .config import LLM_MODEL, PRODUCT_DOMAIN, PRODUCT_NAME
from .ports.base import MERCHANTS, TOOLS
from .ports.setup import setup as setup_merchants


# ── lifecycle ────────────────────────────────────────────────────────────────
def bootstrap(*, vuelaya_url: str | None = None,
              mami_url: str | None = None) -> dict[str, Any]:
    """Call once at process start. Registers merchants and event subscribers."""
    merchants = setup_merchants(vuelaya_url=vuelaya_url, mami_url=mami_url)
    relay.default_subscribers()
    return {"product": PRODUCT_NAME, "domain": PRODUCT_DOMAIN, "model": LLM_MODEL,
            "merchants": merchants,
            "tools_callable": TOOLS.callable_names(),
            "tools_refused": TOOLS.refused}


def health(conn) -> dict[str, Any]:
    chain = audit.verify_all(conn)
    return {"ok": chain["valid"], "chains": chain["chains"],
            "events": chain["checked"], "merchants": sorted(MERCHANTS),
            "outbox": relay.pending(conn)}


# ── the buyer's conversation ─────────────────────────────────────────────────
def ask(conn, *, text: str, agent_id: str, mandate_jti: str,
        session_id: str | None = None, person: str = "buyer") -> dict[str, Any]:
    """A turn. Starts a run, answers an escalation, or redirects one in flight."""
    session = chat.Session(conn, agent_id=agent_id, mandate_jti=mandate_jti,
                           session_id=session_id, person=person)
    replies = session.send(text)
    run = session.active_run()
    return {"session_id": session.session_id, "replies": replies,
            "run": _run_view(run) if run else None,
            "awaiting_human": bool(run and run["status"] == "awaiting_human")}


def transcript(conn, session_id: str) -> list[dict]:
    return chat.transcript(conn, session_id)


def _run_view(run: dict) -> dict[str, Any]:
    state = run["state"]
    return {"run_id": run["run_id"], "status": run["status"], "node": run["node"],
            "agent_version": run["agent_version"],
            "escalation_id": run.get("escalation_id"),
            "proposal": state.get("proposal"), "result": state.get("result")}


# ── the human in the loop ────────────────────────────────────────────────────
def pending_escalations(conn) -> list[dict]:
    return escalation.pending(conn)


def resolve_escalation(conn, *, escalation_id: str, decision: str, token: str,
                       channel: str = "api", sticky: bool = True) -> dict[str, Any]:
    row = escalation.get(conn, escalation_id)
    agent_row = conn.execute("SELECT agent_id FROM mandates WHERE jti=?",
                             (row["mandate_jti"],)).fetchone()
    who = auth.require(conn, token, "escalation.resolve",
                       agent_row["agent_id"] if agent_row else None)
    result = escalation.resolve(conn, escalation_id, decision=decision.upper(),
                                approver=who.person_id, channel=channel,
                                sticky=sticky)
    if row["run_id"]:
        graph.resume(conn, row["run_id"])
    return result


# ── the console ──────────────────────────────────────────────────────────────
def list_agents(conn) -> list[dict]:
    return [dict(r) for r in registry.list_agents(conn)]


def get_agent(conn, agent_id: str) -> dict[str, Any]:
    agent = registry.get_agent(conn, agent_id)
    version = registry.get_version(conn, agent_id)
    return {"agent": dict(agent),
            "ontology": json.loads(version["ontology"]),
            "history": [dict(h) for h in registry.history(conn, agent_id)]}


def publish_ontology(conn, *, agent_id: str, ontology: dict, token: str,
                     reason: str = "") -> dict[str, Any]:
    who = auth.require(conn, token, "agent.publish", agent_id)
    version = registry.publish_version(conn, agent_id, ontology,
                                       changed_by=who.person_id, reason=reason)
    return {"agent_id": agent_id, "version": version, "by": who.person_id}


def create_watch(conn, *, agent_id: str, mandate_jti: str, query: dict,
                 max_price: float, token: str, interval_s: int = 300) -> dict[str, Any]:
    who = auth.require(conn, token, "watch.create", agent_id)
    return watcher.create_watch(
        conn, agent_id=agent_id, mandate_jti=mandate_jti, query=query,
        threshold={"<=": [{"var": "offer.price"}, max_price]},
        interval_s=interval_s, created_by=who.person_id)


def revoke_mandate(conn, *, mandate_jti: str, token: str) -> dict[str, Any]:
    who = auth.require(conn, token, "mandate.revoke")
    return mandate_mod.revoke(conn, mandate_jti, actor=who.person_id)


# ── the control tower ────────────────────────────────────────────────────────
def trail(conn, *, mandate_jti: str | None = None, after_seq: int = 0,
          limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM audit_events WHERE seq > ?"
    args: list[Any] = [after_seq]
    if mandate_jti:
        sql += " AND mandate_jti = ?"
        args.append(mandate_jti)
    sql += " ORDER BY seq LIMIT ?"
    args.append(limit)
    return [{**dict(r), "payload": json.loads(r["payload"])}
            for r in conn.execute(sql, tuple(args)).fetchall()]


def verify(conn) -> dict[str, Any]:
    return {"chains": audit.verify_all(conn),
            "checkpoint": audit.verify_checkpoint(conn)}


def mandate_view(conn, mandate_jti: str) -> dict[str, Any]:
    row = mandate_mod.get(conn, mandate_jti)
    return {"jti": row["jti"], "status": row["status"],
            "spent": row["spent_total"], "reserved": row["reserved_amount"],
            "txn_count": row["txn_count"], "claims": json.loads(row["claims"]),
            "memory": memory.summarise(conn, mandate_jti)}


def events_since(event_id: str | None = None) -> list[dict]:
    """For SSE. The relay keeps a bounded tail; this reads it."""
    buffer = relay.SUBSCRIBERS.get("sse")
    return buffer.since(event_id) if buffer else []


# ── background work ──────────────────────────────────────────────────────────
def tick(conn) -> dict[str, Any]:
    """One scheduler pass: expire escalations, poll watches, drain the outbox."""
    return watcher.tick(conn)


def guardrails(conn) -> dict[str, Any]:
    return limits.snapshot(conn)
