"""Agent bridge router — connecting agent lifecycle, chat, runs, watches, and limits.

Endpoints:
- POST /agent/ask
- GET /agent/transcript
- GET /agent/runs
- GET /agent/watches
- POST /agent/watches
- GET /agent/limits
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
