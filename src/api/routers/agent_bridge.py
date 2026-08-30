"""Agent bridge router — connecting agent lifecycle, chat, runs, watches, and limits.

Endpoints:
- POST /agent/ask
- GET /agent/transcript
- GET /agent/runs
- GET /agent/watches
- POST /agent/watches
- GET /agent/limits
- GET /agent/mandate
- GET /agent/escalations
- GET /agent/audit
- GET /agent/verify
- GET /agent/agents

The last five exist because the agent lane keeps its own SQLite store: its
mandates, escalations and hash chain are NOT the ones behind `/mandates`,
`/escalations` and `/audit/*`, which belong to the kernel lane's registry.
A client that wants to see what *this* agent spent, or what it is parked on,
has to read through here.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import deps

router = APIRouter(prefix="/agent", tags=["agent"])


class AskRequest(BaseModel):
    text: str
    agent_id: str
    mandate_jti: str
    session_id: str | None = None
    person: str = "buyer"


class CreateWatchRequest(BaseModel):
    agent_id: str
    mandate_jti: str
    query: dict[str, Any] = Field(default_factory=dict)
    max_price: float | None = None
    threshold: dict[str, Any] | None = None
    token: str | None = None
    interval_s: int = 300
    autobuy: bool = True
    created_by: str | None = None


class DispatchRequest(BaseModel):
    text: str
    session_id: str | None = None
    person: str = "buyer"


@router.post("/dispatch")
async def dispatch_agent(body: DispatchRequest) -> dict[str, Any]:
    """Route the request to the active agent whose mandate scope matches.

    Deterministic selection over the LLM's category read: the caller never
    hardcodes an agent, and the gate still enforces the mandate anyway.
    """
    conn = deps.agent_conn()
    if conn is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent storage connection is not available",
        )

    from src.agent import router as agent_router, service

    picked = agent_router.select_agent(conn, body.text)
    if picked is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no active agent matches this request"
        )
    result = service.ask(
        conn,
        text=body.text,
        agent_id=picked["agent_id"],
        mandate_jti=picked["mandate_jti"],
        session_id=body.session_id,
        person=body.person,
    )
    return {**result, "dispatch": picked}


@router.post("/ask")
async def ask_agent(body: AskRequest) -> dict[str, Any]:
    """Forward a turn to the agent orchestrator graph."""
    conn = deps.agent_conn()
    if conn is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent storage connection is not available",
        )

    from src.agent import service

    try:
        return service.ask(
            conn,
            text=body.text,
            agent_id=body.agent_id,
            mandate_jti=body.mandate_jti,
            session_id=body.session_id,
            person=body.person,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/transcript")
async def get_transcript(session_id: str = Query(...)) -> list[dict[str, Any]]:
    """Get the full chat transcript for a session."""
    conn = deps.agent_conn()
    if conn is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent storage connection is not available",
        )

    from src.agent import service

    try:
        return service.transcript(conn, session_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/runs")
async def list_runs(
    session_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List agent execution runs from checkpointed storage."""
    conn = deps.agent_conn()
    if conn is None:
        return []

    sql = "SELECT * FROM agent_runs"
    clauses: list[str] = []
    args: list[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        args.append(session_id)
    if agent_id:
        clauses.append("agent_id = ?")
        args.append(agent_id)

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)

    try:
        rows = conn.execute(sql, tuple(args)).fetchall()
        runs: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("state"), str):
                try:
                    d["state"] = json.loads(d["state"])
                except Exception:
                    pass
            runs.append(d)
        return runs
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/watches")
async def list_watches(
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[dict[str, Any]]:
    """List standing watches with human-configured price thresholds."""
    conn = deps.agent_conn()
    if conn is None:
        return []

    from src.agent import watcher

    try:
        return watcher.list_watches(conn, status=status_filter)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/watches")
async def create_watch(body: CreateWatchRequest) -> dict[str, Any]:
    """Create a recurrent search watch."""
    conn = deps.agent_conn()
    if conn is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent storage connection is not available",
        )

    from src.agent import service, watcher

    try:
        if body.token is not None:
            return service.create_watch(
                conn,
                agent_id=body.agent_id,
                mandate_jti=body.mandate_jti,
                query=body.query,
                max_price=body.max_price or 999999.0,
                token=body.token,
                interval_s=body.interval_s,
            )

        threshold = body.threshold
        if threshold is None:
            if body.max_price is not None:
                threshold = {"<=": [{"var": "offer.price"}, body.max_price]}
            else:
                threshold = {"<=": [{"var": "offer.price"}, 999999.0]}

        return watcher.create_watch(
            conn,
            agent_id=body.agent_id,
            mandate_jti=body.mandate_jti,
            query=body.query,
            threshold=threshold,
            interval_s=body.interval_s,
            autobuy=body.autobuy,
            created_by=body.created_by,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/limits")
async def get_limits() -> dict[str, Any]:
    """Get telemetry, token buckets, rolling counters, and held locks."""
    conn = deps.agent_conn()
    if conn is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent storage connection is not available",
        )

    from src.agent import limits

    try:
        return limits.snapshot(conn)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _conn() -> Any:
    conn = deps.agent_conn()
    if conn is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Agent storage connection is not available",
        )
    return conn


def _active_mandate(conn) -> str | None:
    """The oldest live mandate.

    Ordered on purpose: an unordered `LIMIT 1` returns whichever row Postgres
    reaches first, and that moves as soon as a purchase updates one. A caller
    that reads a balance here and then spends elsewhere has to get the same
    mandate both times.
    """
    row = conn.execute(
        "SELECT jti FROM mandates WHERE status = 'active' ORDER BY created_at, jti LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT jti FROM mandates ORDER BY created_at, jti LIMIT 1").fetchone()
    return row["jti"] if row else None


@router.get("/mandate")
async def get_mandate(jti: str | None = Query(default=None)) -> dict[str, Any]:
    """The authority the agent is spending under: claims, spend, memory."""
    conn = _conn()
    target = jti or _active_mandate(conn)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mandate has been issued")

    from src.agent import service

    try:
        return service.mandate_view(conn, target)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/escalations")
async def list_pending_escalations() -> list[dict[str, Any]]:
    """Every run parked on a human. Silence never approves one — they expire."""
    conn = _conn()

    from src.agent import service

    try:
        return service.pending_escalations(conn)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/audit")
async def get_audit_trail(
    mandate_jti: str | None = Query(default=None),
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """The agent lane's own hash chain, oldest first."""
    conn = _conn()

    from src.agent import service

    try:
        return service.trail(conn, mandate_jti=mandate_jti, after_seq=after_seq, limit=limit)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/verify")
async def verify_chain() -> dict[str, Any]:
    """Recompute every chain and check the signed checkpoint."""
    conn = _conn()

    from src.agent import service

    try:
        return service.verify(conn)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/agents")
async def list_configured_agents() -> list[dict[str, Any]]:
    """The configuration console: who owns each agent and which version is live."""
    conn = _conn()

    from src.agent import service

    try:
        return service.list_agents(conn)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
