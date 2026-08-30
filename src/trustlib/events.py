"""Appending to the outbox — the one way any workstream emits an event.

Decision 0022. Dev 2 owns the `outbox` DDL; Dev 3 emits eight of the event
types in `schemas.md` §4. PLAN-PARALELO §6.4 forbids writing another
workstream's table, but decision #10 *requires* the event and the business
change to commit in one transaction — an API call between them would break
exactly the atomicity the outbox exists to provide.

So: schema ownership stays with Dev 2, write access goes through this
function. It takes the caller's session, never opens its own.

It is also the single place the envelope of §4 is enforced, so a misspelled
type fails here instead of three routers away.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from . import ids
from .models import EventEnvelope

# schemas.md §4. Emitting a type that is not here is a contract change.
KNOWN_EVENT_TYPES = frozenset({
    # [3] identity
    "mandate.created", "mandate.activated", "mandate.revoked",
    "mandate.suspended", "mandate.exhausted", "mandate.expired",
    "payment_instrument.linked",
    # [3] human in the loop
    "escalation.resolved", "escalation.expired",
    # [1] agent
    "offer.seen",
    # [2] purchase saga
    "purchase.requested", "purchase.verified", "purchase.escalated",
    "purchase.captured", "purchase.rejected",
    # rail (was payment.*/dispute.* from PayPal; now from the orchestrator)
    "payment.captured", "payment.refused",
    "dispute.opened", "dispute.resolved",
    # [2] evidence
    "root.checkpoint",
})

_INSERT = text("""
    INSERT INTO outbox (event_id, type, aggregate_id, payload, created_at)
    VALUES (:event_id, :type, :aggregate_id, CAST(:payload AS JSONB), :created_at)
""")


class UnknownEventType(ValueError):
    """A type absent from the §4 catalogue. Fail loudly, not silently."""


async def emit_event(session, *, type: str, aggregate_id: str,
                     payload: dict[str, Any]) -> EventEnvelope:
    """Append one event inside the caller's transaction.

    No commit here. The caller's transaction boundary decides, which is the
    whole point — if the business change rolls back, so does its event.
    """
    if type not in KNOWN_EVENT_TYPES:
        raise UnknownEventType(
            f"{type!r} is not in the schemas.md §4 catalogue. "
            "Adding an event type is a contract change (PLAN-PARALELO §6.2)."
        )

    import json

    envelope = EventEnvelope(
        event_id=ids.new_id(ids.EVENT),
        type=type,
        aggregate_id=aggregate_id,
        payload=payload,
        created_at=datetime.now(UTC),
    )
    await session.execute(_INSERT, {
        "event_id": envelope.event_id,
        "type": envelope.type,
        "aggregate_id": envelope.aggregate_id,
        "payload": json.dumps(envelope.payload, default=str),
        "created_at": envelope.created_at,
    })
    return envelope
