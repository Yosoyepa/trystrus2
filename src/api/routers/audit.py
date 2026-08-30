"""Audit router — hash chain verification, events inspection, and tamper demo.

Endpoints:
- GET /audit/events
- POST /audit/verify
- GET /audit/verify
- POST /audit/tamper
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status

from .. import deps

router = APIRouter(tags=["audit"])


@router.get("/audit/events")
async def list_audit_events(
    mandate_id: str | None = Query(default=None),
    mandate_jti: str | None = Query(default=None),
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """List audit events optionally filtered by mandate, paginated by seq."""
    service = deps.ledger_service()
    repo = getattr(service, "_repo", None)
    if repo is None:
        return []

    target = mandate_id or mandate_jti
    if target:
        events = repo.get_by_mandate(target)
    else:
        events = repo.get_all()

    filtered = [e for e in events if e.seq is not None and e.seq > after_seq]
    limited = filtered[:limit]

    return [e.to_dict() for e in limited]


@router.post("/audit/verify")
async def verify_audit_post(
    mandate_id: str | None = Query(default=None),
    mandate_jti: str | None = Query(default=None),
    seq_start: int | None = Query(default=None),
    seq_end: int | None = Query(default=None),
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Recompute all chains or a specific chain, check against root signer and witness."""
    service = deps.ledger_service()
    b = body or {}

    target = mandate_id or mandate_jti or b.get("mandate_id") or b.get("mandate_jti")
    start = seq_start or b.get("seq_start")
    end = seq_end or b.get("seq_end")

    seq_range = (int(start), int(end)) if (start is not None and end is not None) else None

    try:
        result = service.verify_chain(mandate_id=target, seq_range=seq_range)
    except Exception as exc:
        return {
            "valid": False,
            "ok": False,
            "error": str(exc),
            "reason": str(exc),
            "events_checked": 0,
            "verified_count": 0,
        }

    return {
        "valid": result.ok,
        "ok": result.ok,
        "first_bad_seq": result.first_bad_seq,
        "reason": result.reason,
        "error": result.reason,
        "events_checked": result.verified_count,
        "verified_count": result.verified_count,
        "last_hash": result.last_hash,
    }


@router.get("/audit/verify")
async def verify_audit_get(
    mandate_id: str | None = Query(default=None),
    mandate_jti: str | None = Query(default=None),
    seq_start: int | None = Query(default=None),
    seq_end: int | None = Query(default=None),
) -> dict[str, Any]:
    """Recompute hash chain integrity and witness check via GET."""
    return await verify_audit_post(
        mandate_id=mandate_id,
        mandate_jti=mandate_jti,
        seq_start=seq_start,
        seq_end=seq_end,
        body=None,
    )


@router.post("/audit/tamper")
async def tamper_audit_event(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Demo endpoint mutating an audit event byte in storage to show tamper detection."""
    service = deps.ledger_service()
    repo = getattr(service, "_repo", None)
    if repo is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "No ledger repository configured to tamper",
        )

    seq = int(body.get("seq", 1))
    field_name = str(body.get("field_name") or body.get("field") or "payload")
    value = body.get("value", "TAMPERED_DEMO_MUTATION")

    if not hasattr(repo, "tamper"):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Configured ledger repository does not support in-memory tamper hook",
        )

    try:
        repo.tamper(seq, field_name, value)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return {
        "status": "tampered",
        "seq": seq,
        "field": field_name,
        "value": value,
    }
