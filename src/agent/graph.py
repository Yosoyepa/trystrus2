"""The agent's orchestrator: an explicit graph we own (decision #16).

Nodes: perceive -> search -> propose -> gate -> [await_human] -> receipt -> done

Three properties live in this file and nowhere else:

  E7/E8  Every node transition persists to `agent_runs` and emits an audit
         event, with the agent_version PINNED at run start.  The trajectory is
         evidence, and a run can be replayed against the brain it actually had.
  S7     `await_human` persists and RETURNS.  The resume re-enters `gate` -- it
         never jumps to pay.  An approval authorises a retry.
  S4     `perceive` assembles ontology + memory + run state for the model.  None
         of it is passed to the gate; the gate reads the signed mandate.

The graph is a dict of plain functions on purpose: if this ever needs a
framework, the same functions wrap unchanged.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from . import audit, limits, llm, memory, registry
from . import ontology as ontology_mod
from .ids import new_id, now_iso
from .ports.base import search_all

MAX_REPLANS = 2
STOP, PAUSE = "__stop__", "__pause__"

# Which refusals are worth trying again.  Retrying a revoked mandate fills the
# log with noise and reads as a broken agent; the price moving under us is a
# genuine reason to look again.
RETRYABLE = {"AMOUNT_MISMATCH", "RAIL_ERROR"}
TERMINAL = {
    "MANDATE_REVOKED",
    "MANDATE_EXPIRED",
    "MANDATE_SUSPENDED",
    "MANDATE_EXHAUSTED",
    "MANDATE_NOT_YET_VALID",
    "BUDGET_EXCEEDED",
    "LIMIT_EXHAUSTED",
    "CATEGORY_FORBIDDEN",
    "MERCHANT_NOT_ALLOWED",
    "INVALID_SIGNATURE",
    "INVALID_PROOF_OF_POSSESSION",
    "DUPLICATE_JTI",
    "NONCE_REUSED",
    "HUMAN_REJECTED",
    "ESCALATION_TIMEOUT_DENIED",
    "RAIL_TOKEN_DELETED",
}


# ── run persistence ──────────────────────────────────────────────────────────
def _load(conn, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such run: {run_id}")
    return {**dict(row), "state": json.loads(row["state"])}


def _save(
    conn,
    run: dict,
    *,
    node: str,
    status: str | None = None,
    escalation_id: str | None = None,
    event: dict | None = None,
) -> dict:
    status = status or run["status"]
    conn.execute(
        "UPDATE agent_runs SET node=?, state=?, status=?, escalation_id=?, updated_at=?"
        " WHERE run_id=?",
        (
            node,
            json.dumps(run["state"]),
            status,
            escalation_id if escalation_id is not None else run.get("escalation_id"),
            now_iso(),
            run["run_id"],
        ),
    )
    audit.append(
        conn,
        "agent.node.entered",
        {
            "run_id": run["run_id"],
            "node": node,
            "status": status,
            "agent_version": run["agent_version"],
            **(event or {}),
        },
        agent_id=run["agent_id"],
        run_id=run["run_id"],
        mandate_jti=run["mandate_jti"],
    )
    run["node"], run["status"] = node, status
    if escalation_id is not None:
        run["escalation_id"] = escalation_id
    return run


def start(
    conn, *, agent_id: str, mandate_jti: str, request: str, session_id: str | None = None
) -> dict[str, Any]:
    agent = registry.get_agent(conn, agent_id)
    if agent["status"] != "active":
        raise ValueError(f"agent {agent_id} is {agent['status']}")
    limits.guard_run_start(conn, agent_id)  # bounded runs per agent per hour
    version = int(agent["current_version"])  # E8/K3: pinned now, never re-read
    run_id = new_id("run")
    stamp = now_iso()
    state = {"request": request, "guidance": [], "replans": 0, "messages": []}
    conn.execute(
        "INSERT INTO agent_runs(run_id,agent_id,agent_version,mandate_jti,session_id,"
        "node,state,status,created_at,updated_at) VALUES(?,?,?,?,?,'perceive',?, "
        "'running',?,?)",
        (run_id, agent_id, version, mandate_jti, session_id, json.dumps(state), stamp, stamp),
    )
    audit.append(
        conn,
        "agent.run.started",
        {"run_id": run_id, "agent_version": version, "request": request},
        agent_id=agent_id,
        run_id=run_id,
        mandate_jti=mandate_jti,
    )
    return _load(conn, run_id)


# ── the nodes ────────────────────────────────────────────────────────────────
def node_perceive(conn, run: dict) -> str:
    """Assemble the context. Ontology + history + run state. Never the gate (S4)."""
    version = registry.get_version(conn, run["agent_id"], run["agent_version"])
    onto = json.loads(version["ontology"])
    mandate_row = conn.execute(
        "SELECT claims FROM mandates WHERE jti=?", (run["mandate_jti"],)
    ).fetchone()
    claims = json.loads(mandate_row["claims"]) if mandate_row else {}
    # The scope is a filter here only to save pointless calls; the gate enforces
    # it regardless, so a merchant the buyer never allowed can never be bought from.
    run["state"]["allowed_merchants"] = (claims.get("scope") or {}).get("merchants")
    run["state"]["currency"] = claims.get("currency", "USD")
    summary = memory.summarise(conn, run["mandate_jti"])
    run["state"]["ontology_text"] = limits.clamp_text(ontology_mod.render(onto))
    run["state"]["memory"] = summary
    run["state"]["memory_text"] = memory.render(summary)
    limits.guard_llm_call(conn, run["agent_id"], run["mandate_jti"])
    run["state"]["criteria"] = llm.parse_request(
        run["state"]["request"]
        + (" " + " ".join(run["state"]["guidance"]) if run["state"]["guidance"] else "")
    )
    _save(
        conn,
        run,
        node="perceive",
        event={
            "criteria": run["state"]["criteria"],
            "memory": {
                "purchases_made": summary["purchases_made"],
                "total_spent": summary["total_spent"],
            },
        },
    )
    return "search"


def node_search(conn, run: dict) -> str:
    """MCP tools. Read-only, cost nothing, commit nothing."""
    criteria = run["state"].get("criteria") or {}
    limits.guard_merchant_call(conn, run["agent_id"], run["mandate_jti"])
    allowed = run["state"].get("allowed_merchants")
    offers = search_all(
        conn,
        allowed=allowed,
        origin=criteria.get("origin"),
        destination=criteria.get("destination"),
        date=criteria.get("date"),
        category=criteria.get("category"),
        query=run["state"]["request"],
    )
    if not offers:  # a filter that matched nothing is worse than a broad list
        offers = search_all(
            conn, allowed=allowed, category=criteria.get("category"), query=run["state"]["request"]
        )
    run["state"]["offers"] = limits.clamp_offers(offers)
    offers = run["state"]["offers"]
    _save(conn, run, node="search", event={"offers_found": len(offers)})
    audit.append(
        conn,
        "offer.seen",
        {
            "run_id": run["run_id"],
            "count": len(offers),
            "cheapest": offers[0]["price"] if offers else None,
        },
        agent_id=run["agent_id"],
        run_id=run["run_id"],
        mandate_jti=run["mandate_jti"],
    )
    return "propose" if offers else "denied"


def node_propose(conn, run: dict) -> str:
    """The only node with a model in it (S1)."""
    state = run["state"]
    limits.guard_llm_call(conn, run["agent_id"], run["mandate_jti"])
    proposal = llm.propose(
        request=state["request"],
        offers=state["offers"],
        ontology_text=state.get("ontology_text", ""),
        memory_text=state.get("memory_text", ""),
        guidance=" ".join(state.get("guidance", [])),
    )
    state["proposal"] = proposal
    _save(
        conn,
        run,
        node="propose",
        event={
            "offer_id": proposal.get("offer_id"),
            "source": proposal.get("source"),
            "concern": proposal.get("concern"),
        },
    )
    return "gate" if proposal.get("offer_id") else "denied"


def node_gate(conn, run: dict) -> str:
    """Hand the proposal to the kernel. We do not decide; we submit.

    Note what is NOT passed: no amount, no ontology, no history.  Just the offer
    id and the mandate (S4, S6).
    """
    from . import kernel

    state = run["state"]
    _save(conn, run, node="gate")
    chosen = next(
        (o for o in state["offers"] if o["offer_id"] == state["proposal"]["offer_id"]), None
    )
    result = kernel.submit_purchase(
        conn,
        offer_id=state["proposal"]["offer_id"],
        mandate_jti=run["mandate_jti"],
        merchant_id=(chosen or {}).get("merchant_id"),
        run_id=run["run_id"],
    )
    state["result"] = result
    if result["status"] == "captured":
        return "receipt"
    if result["status"] == "escalated":
        return "await_human"
    reason = result.get("reason_code")
    if reason in RETRYABLE and state.get("replans", 0) < MAX_REPLANS:
        state["replans"] = state.get("replans", 0) + 1
        state.setdefault("guidance", []).append(
            f"the previous choice failed with {reason}; pick a different offer"
        )
        return "search"
    return "denied"


def node_await_human(conn, run: dict) -> str:
    """Persist and RETURN. The run survives a restart here (decision #16)."""
    result = run["state"]["result"]
    _save(
        conn,
        run,
        node="await_human",
        status="awaiting_human",
        escalation_id=result["escalation_id"],
        event={
            "escalation_id": result["escalation_id"],
            "reason_code": result.get("reason_code"),
            "diff": result.get("diff", {}),
        },
    )
    return "__pause__"


def node_receipt(conn, run: dict) -> str:
    _save(conn, run, node="receipt", event={"receipt": run["state"]["result"].get("receipt")})
    return "done"


def node_done(conn, run: dict) -> str:
    _save(conn, run, node="done", status="done")
    return "__stop__"


def node_denied(conn, run: dict) -> str:
    result = run["state"].get("result") or {}
    _save(
        conn,
        run,
        node="denied",
        status="denied",
        event={"reason_code": result.get("reason_code"), "detail": result.get("detail", "")},
    )
    return "__stop__"


NODES: dict[str, Callable] = {
    "perceive": node_perceive,
    "search": node_search,
    "propose": node_propose,
    "gate": node_gate,
    "await_human": node_await_human,
    "receipt": node_receipt,
    "done": node_done,
    "denied": node_denied,
}


# ── the loop ─────────────────────────────────────────────────────────────────
def run_until_pause(
    conn, run_id: str, *, max_steps: int | None = None, max_seconds: int | None = None
) -> dict[str, Any]:
    """Advance until the run finishes or needs a human.

    Two independent stops, because a loop can be slow without being long and
    long without being slow: a step budget and a wall clock.  Either one
    exhausting fails the run closed -- a run that cannot finish never pays.
    """
    # `or` would treat an explicit 0 as "unset" and hand back the default --
    # a caller asking for no budget at all would get the largest one.
    max_steps = limits.QUOTA.max_steps_per_run if max_steps is None else max_steps
    budget_s = limits.QUOTA.max_run_seconds if max_seconds is None else max_seconds
    deadline = time.monotonic() + budget_s
    run = _load(conn, run_id)
    if run["status"] in ("done", "denied", "failed"):
        return run
    node = run["node"] if run["status"] == "running" else "perceive"
    for _ in range(max_steps):
        if time.monotonic() >= deadline:
            _save(
                conn,
                run,
                node=node,
                status="failed",
                event={"error": "run exceeded its wall clock", "guardrail": True},
            )
            return _load(conn, run_id)
        handler = NODES.get(node)
        if handler is None:
            _save(conn, run, node=node, status="failed", event={"error": f"unknown node {node}"})
            return _load(conn, run_id)
        try:
            node = handler(conn, run)
        except limits.LimitExceeded as exc:  # throttled is denied, never paid
            _save(
                conn,
                run,
                node=node,
                status="denied",
                event={"reason_code": exc.code, "detail": exc.detail, "guardrail": True},
            )
            run["state"]["result"] = {
                "status": "rejected",
                "reason_code": exc.code,
                "detail": exc.detail,
            }
            return _load(conn, run_id)
        except Exception as exc:  # a crashed run is a denied run, never a paid one
            _save(conn, run, node=node, status="failed", event={"error": str(exc)})
            return _load(conn, run_id)
        if node in ("__pause__", "__stop__"):
            return _load(conn, run_id)
    _save(conn, run, node=node, status="failed", event={"error": "step budget exhausted"})
    return _load(conn, run_id)


def resume(conn, run_id: str) -> dict[str, Any]:
    """Called after an escalation resolves. Re-enters through the gate (S7).

    Idempotent (M5): the escalation is already resolved, so calling this twice
    reads the same settled outcome rather than charging again.
    """
    run = _load(conn, run_id)
    if run["status"] != "awaiting_human":
        return run
    esc = conn.execute("SELECT * FROM escalations WHERE id=?", (run["escalation_id"],)).fetchone()
    if esc is None or esc["status"] == "pending":
        return run
    purchase = conn.execute("SELECT * FROM purchases WHERE id=?", (esc["purchase_id"],)).fetchone()
    outcome = {
        "status": purchase["status"],
        "reason_code": purchase["reason_code"],
        "purchase_id": purchase["id"],
        "receipt": json.loads(purchase["receipt"]) if purchase["receipt"] else None,
    }
    run["state"]["result"] = outcome
    run["status"] = "running"
    conn.execute("UPDATE agent_runs SET status='running' WHERE run_id=?", (run_id,))
    node = "receipt" if purchase["status"] == "captured" else "denied"
    _save(conn, run, node="gate", event={"resumed_after": esc["id"], "decision": esc["decision"]})
    NODES[node](conn, run)
    return _load(conn, run_id)


def add_guidance(conn, run_id: str, text: str) -> dict[str, Any]:
    """Mid-run feedback from the human: replan with what they just said."""
    run = _load(conn, run_id)
    run["state"].setdefault("guidance", []).append(text)
    run["state"]["replans"] = 0
    conn.execute(
        "UPDATE agent_runs SET state=?, status='running', updated_at=? WHERE run_id=?",
        (json.dumps(run["state"]), now_iso(), run_id),
    )
    audit.append(
        conn,
        "agent.guidance.received",
        {"run_id": run_id, "guidance": text},
        agent_id=run["agent_id"],
        run_id=run_id,
        mandate_jti=run["mandate_jti"],
    )
    return _load(conn, run_id)
