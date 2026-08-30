"""HTTP surface for human-in-the-loop escalation resolution."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib.models import Escalation, EscalationStatus

from ..db import get_session
from ..schemas import ResolveRequest
from ..services import escalations

router = APIRouter(prefix="/escalations", tags=["escalations"])


@router.get("", response_model=list[Escalation])
async def list_escalations(
    mandate_id: str | None = None,
    status: EscalationStatus | None = None,
    session: AsyncSession = Depends(get_session),
):
    return await escalations.list_escalations(session, mandate_id=mandate_id, status=status)


@router.post("/{escalation_id}/resolve", response_model=Escalation)
async def resolve_escalation(
    escalation_id: str,
    body: ResolveRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        return await escalations.resolve(
            session,
            escalation_id=escalation_id,
            decision=body.decision,
            approver=body.approver,
            channel=body.channel,
            sticky=body.sticky,
        )
    except escalations.EscalationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such escalation") from exc
    except escalations.EscalationConflict as exc:
        detail = exc.reason_code.value if exc.reason_code else str(exc)
        raise HTTPException(status.HTTP_409_CONFLICT, detail) from exc
