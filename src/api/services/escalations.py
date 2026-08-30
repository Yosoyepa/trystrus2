"""Human-in-the-loop escalation lifecycle (schemas.md §5)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib import ids
from trustlib.events import emit_event
from trustlib.jose import sign_compact
from trustlib.models import Escalation as EscalationView
from trustlib.models import EscalationResolution, EscalationStatus, ReasonCode

from ..config import settings
from ..db import session_factory
from ..models import Escalation, iso_now
from .keys import key_store


class EscalationNotFound(Exception):
    pass


class EscalationConflict(Exception):
    def __init__(self, message: str, *, reason_code: ReasonCode | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _iso(moment: datetime) -> str:
    """`escalations.timeout_at`/`resolved_at`/`created_at` are TEXT (the agent
    lane's table, shared verbatim). One fixed, zero-padded format is what
    makes `<`/`>=` on those columns sort the same way TIMESTAMPTZ did."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("escalation timestamps must be timezone-aware")
    return moment.astimezone(UTC).replace(microsecond=0).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def create(
    session: AsyncSession,
    *,
    purchase_id: str,
    mandate_id: str,
    diff: dict | None,
    timeout_at: datetime | None = None,
) -> EscalationView:
    """Helper for Dev 2's saga: an escalation begins pending and expires soon."""
    deadline = timeout_at or datetime.now(UTC) + timedelta(
        seconds=settings().escalation_timeout_seconds
    )
    row = Escalation(
        id=ids.new_id(ids.ESCALATION),
        purchase_id=purchase_id,
        mandate_jti=mandate_id,
        status=EscalationStatus.PENDING.value,
        diff=json.dumps(diff) if diff is not None else None,
        timeout_at=_iso(deadline),
        created_at=iso_now(),
    )
    session.add(row)
    await session.flush()
    return _view(row)


async def expire_pending(session: AsyncSession, *, now: datetime | None = None) -> list[str]:
    """The lazy fail-closed half: one guarded update plus durable events."""
    moment = now or datetime.now(UTC)
    result = await session.execute(
        update(Escalation)
        .where(
            Escalation.status == EscalationStatus.PENDING.value,
            Escalation.timeout_at < _iso(moment),
        )
        .values(status=EscalationStatus.EXPIRED.value, decision="REJECT")
        .returning(Escalation.id, Escalation.purchase_id)
    )
    expired = list(result.all())
    for row in expired:
        await emit_event(
            session,
            type="escalation.expired",
            aggregate_id=row.purchase_id,
            payload={"escalation_id": row.id},
        )
    return [row.id for row in expired]


async def list_escalations(
    session: AsyncSession,
    *,
    mandate_id: str | None = None,
    status: EscalationStatus | None = None,
) -> list[EscalationView]:
    # The sweeper may be down; a read still refuses a past deadline.
    await expire_pending(session)
    statement = select(Escalation)
    if mandate_id:
        statement = statement.where(Escalation.mandate_jti == mandate_id)
    if status:
        statement = statement.where(Escalation.status == status.value)
    statement = statement.order_by(Escalation.created_at.desc())
    rows = (await session.execute(statement)).scalars().all()
    return [_view(row) for row in rows]


async def resolve(
    session: AsyncSession,
    *,
    escalation_id: str,
    decision: str,
    approver: str,
    channel: str,
    sticky: dict | None = None,
    now: datetime | None = None,
) -> EscalationView:
    """Record a human answer and publish the event the saga consumes.

    APPROVE produces a signed receipt but does not mutate a purchase or a
    limit.  Dev 2 has to run the gate again after receiving it, which is why
    an approval can never act as a policy bypass.
    """
    if sticky is not None:
        raise EscalationConflict(
            "sticky approvals need a returned derived mandate and are not "
            "enabled by this response contract"
        )

    moment = now or datetime.now(UTC)
    await expire_pending(session, now=moment)

    row = await session.get(Escalation, escalation_id)
    if row is None:
        raise EscalationNotFound(escalation_id)
    if row.status == EscalationStatus.EXPIRED.value:
        raise EscalationConflict(
            "escalation timed out", reason_code=ReasonCode.ESCALATION_TIMEOUT_DENIED
        )
    if row.status != EscalationStatus.PENDING.value:
        raise EscalationConflict("escalation was already resolved")

    receipt_payload = {
        "escalation_id": escalation_id,
        "decision": decision,
        "approver": approver,
        "channel": channel,
        "resolved_at": moment.isoformat(),
    }
    signing = key_store().issuer_key()
    receipt_sig = sign_compact(
        receipt_payload, signing.key, kid=signing.kid, typ="escalation-receipt+jwt"
    )

    # The deadline and pending state belong in the UPDATE; a read-then-write
    # lets a late Approve race an expiry, violating the 120s fail-closed rule.
    result = await session.execute(
        update(Escalation)
        .where(
            Escalation.id == escalation_id,
            Escalation.status == EscalationStatus.PENDING.value,
            Escalation.timeout_at >= _iso(moment),
        )
        .values(
            status=EscalationStatus.RESOLVED.value,
            decision=decision,
            approver=approver,
            channel=channel,
            receipt_sig=receipt_sig,
        )
        .returning(Escalation)
    )
    resolved = result.scalar_one_or_none()
    if resolved is None:
        # A concurrent lazy sweep or resolver won. Re-read only to explain;
        # never retry and accidentally turn a timeout into an approval.
        current = await session.get(Escalation, escalation_id)
        if current is not None and current.status == EscalationStatus.EXPIRED.value:
            raise EscalationConflict(
                "escalation timed out", reason_code=ReasonCode.ESCALATION_TIMEOUT_DENIED
            )
        raise EscalationConflict("escalation was already resolved")

    if decision == "APPROVE":
        await emit_event(
            session,
            type="escalation.resolved",
            aggregate_id=resolved.purchase_id,
            payload={
                "escalation_id": resolved.id,
                "decision": decision,
                "receipt_sig": receipt_sig,
            },
        )
    else:
        # schemas.md §5: a human REJECT and a timeout both compensate the
        # saga. The row preserves that this was an explicit answer.
        await emit_event(
            session,
            type="escalation.expired",
            aggregate_id=resolved.purchase_id,
            payload={"escalation_id": resolved.id},
        )
    return _view(resolved)


async def sweep_forever(stop: asyncio.Event) -> None:
    """Background half of the timeout guarantee; reads stay the other half."""
    interval = min(5, max(1, settings().escalation_timeout_seconds // 24))
    while not stop.is_set():
        try:
            async with session_factory()() as session:
                await expire_pending(session)
                await session.commit()
        except Exception:
            # The lazy check in list/resolve still denies late actions. A
            # failed sweeper must never make silence look like approval.
            import logging

            logging.getLogger(__name__).exception("escalation expiry sweep failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def _view(row: Escalation) -> EscalationView:
    resolution = None
    if row.decision:
        resolution = EscalationResolution(
            decision=row.decision,
            approver=row.approver or "system",
            channel=row.channel or "web",
            resolved_at=_from_iso(row.created_at),
            receipt_sig=row.receipt_sig,
        )
    return EscalationView(
        escalation_id=row.id,
        mandate_id=row.mandate_jti,
        purchase_id=row.purchase_id,
        status=EscalationStatus(row.status),
        diff=json.loads(row.diff) if row.diff else None,
        timeout_at=_from_iso(row.timeout_at),
        resolution=resolution,
    )
