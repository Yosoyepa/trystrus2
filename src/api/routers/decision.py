"""Decision and verify router — connecting to Dev 2's DecisionService.

Endpoints:
- POST /mandates/{mandate_id}/verify
- POST /purchases/verify
- POST /purchases
- GET /purchases/{purchase_id}
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, status

from .. import deps

router = APIRouter(tags=["decision"])


def _extract_intent(body: dict[str, Any], fallback_mandate_id: str | None = None) -> dict[str, Any]:
    """Extract or construct purchase intent dictionary from flexible request bodies."""
    intent: dict[str, Any] | None = None

    if "intent" in body and isinstance(body["intent"], dict):
        intent = dict(body["intent"])
    elif "intent_jwt" in body and isinstance(body["intent_jwt"], str):
        token = body["intent_jwt"]
        parts = token.split(".")
        if len(parts) >= 2 and parts[1]:
            try:
                padding = "=" * (-len(parts[1]) % 4)
                decoded = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
                if isinstance(decoded, dict):
                    intent = decoded
            except Exception:
                intent = None

    if intent is None:
        intent = {k: v for k, v in body.items() if k not in ("idempotency_key", "agent_id")}

    if fallback_mandate_id and not intent.get("mandate_jti"):
        intent["mandate_jti"] = fallback_mandate_id

    if not intent.get("jti"):
        uid = uuid.uuid4().hex[:8]
        intent["jti"] = f"intent-{fallback_mandate_id or 'anon'}-{uid}"

    return intent


def _format_verification_result(result: Any) -> dict[str, Any]:
    decision_val = (
        result.decision.decision.value
        if hasattr(result.decision.decision, "value")
        else str(result.decision.decision)
    )
    reason_val = (
        result.decision.reason_code.value
        if hasattr(result.decision.reason_code, "value")
        else result.decision.reason_code
    )
    return {
        "decision": decision_val,
        "reason_code": reason_val,
        "reservation_id": result.reservation_id,
        "expires_in": getattr(result.decision, "ttl_seconds", None) or 120,
        "diff": (
            dict(result.decision.diff or {})
            if getattr(result.decision, "diff", None)
            else None
        ),
        "purchase_id": result.purchase_id,
        "status": result.status,
        "escalation_id": result.escalation_id,
        "requires_uv": getattr(result.decision, "requires_uv", False),
        "level": (
            getattr(result.decision.level, "value", result.decision.level)
            if hasattr(result.decision, "level")
            else None
        ),
    }


@router.post("/mandates/{mandate_id}/verify")
async def verify_mandate(
    mandate_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Verify purchase in pay-time against a specific mandate."""
    service = deps.decision_service()
    intent = _extract_intent(body, fallback_mandate_id=mandate_id)
    purchase_id = body.get("purchase_id") or (
        f"purchase-{intent.get('jti')}" if intent.get("jti") else None
    )
    idempotency_key = body.get("idempotency_key")

    try:
        result = service.verify(
            intent,
            purchase_id=purchase_id,
            idempotency_request=body if idempotency_key else None,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return _format_verification_result(result)


@router.post("/purchases/verify")
async def verify_purchase(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Verify purchase intent through the policy gate and reservation store."""
    service = deps.decision_service()
    intent = _extract_intent(body)
    purchase_id = body.get("purchase_id") or (
        f"purchase-{intent.get('jti')}" if intent.get("jti") else None
    )
    idempotency_key = body.get("idempotency_key")

    try:
        result = service.verify(
            intent,
            purchase_id=purchase_id,
            idempotency_request=body if idempotency_key else None,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return _format_verification_result(result)


@router.post("/purchases", status_code=status.HTTP_202_ACCEPTED)
async def submit_purchase(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Agent purchase submission entry point."""
    service = deps.decision_service()
    intent = _extract_intent(body)
    purchase_id = body.get("purchase_id") or (
        f"purchase-{intent.get('jti')}" if intent.get("jti") else None
    )
    idempotency_key = body.get("idempotency_key")

    try:
        result = service.verify(
            intent,
            purchase_id=purchase_id,
            idempotency_request=body if idempotency_key else None,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    formatted = _format_verification_result(result)
    if result.is_rejected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason_code": formatted["reason_code"],
                "purchase_id": formatted["purchase_id"],
                "message": f"Purchase was rejected: {formatted['reason_code']}",
            },
        )

    return {
        "purchase_id": formatted["purchase_id"],
        "status": formatted["status"],
        "reason_code": formatted["reason_code"],
        "escalation_id": formatted["escalation_id"],
        "reservation_id": formatted["reservation_id"],
    }


@router.get("/purchases/{purchase_id}")
async def get_purchase(purchase_id: str) -> dict[str, Any]:
    """Get status of a purchase."""
    service = deps.decision_service()
    store = getattr(service, "purchase_store", None)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no purchase store configured")

    getter = getattr(store, "get", None) or getattr(store, "get_by_id", None)
    purchase = getter(purchase_id) if getter else None
    if purchase is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"purchase {purchase_id} not found")

    return {
        "purchase_id": getattr(purchase, "purchase_id", purchase_id),
        "status": getattr(purchase, "status", "unknown"),
        "reason_code": getattr(purchase, "reason_code", None),
        "escalation_id": getattr(purchase, "escalation_id", None),
        "reservation_id": getattr(purchase, "reservation_id", None),
    }
