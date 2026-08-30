"""Evidence pack router — canonical evidence envelope assembly (R-EVIDENCE).

Endpoints:
- GET /purchases/{purchase_id}/evidence-pack
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from src.api.evidence.service import EvidenceNotFoundError

from .. import deps

router = APIRouter(tags=["evidence"])


@router.get("/purchases/{purchase_id}/evidence-pack")
async def get_evidence_pack(purchase_id: str) -> dict[str, Any]:
    """Assemble and return the canonical evidence pack for a purchase."""
    service = deps.evidence_service()
    now = datetime.now(UTC)
    try:
        pack = service.assemble(purchase_id, now=now)
    except EvidenceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    return pack.to_dict()
