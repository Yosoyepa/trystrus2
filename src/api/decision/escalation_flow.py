"""Escalation lifecycle helpers for the DEV2 verify path."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.api.domain.models import Decision, DecisionValue, Escalation, EscalationLevel, ReasonCode
from src.api.domain.policy import (
    escalation_deadline,
    escalation_expired,
)
from src.api.domain.policy import (
    resolve_escalation as resolve_domain_escalation,
)

from .ports import EscalationRecord


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def create_escalation(
    *,
    purchase_id: str,
    mandate_id: str,
    intent: Any,
    offer: Any,
    decision: Decision,
    now: datetime,
    escalation_id: str | None = None,
) -> EscalationRecord:
    """Create a durable timeout envelope without reserving any budget."""

    created_at = _utc(now)
    level = decision.level or EscalationLevel.L3
    timeout_at = escalation_deadline(created_at, level)
    diff = dict(decision.diff or {})
    diff.update(
        {
            "level": level.value if isinstance(level, EscalationLevel) else str(level),
            "timeout_at": timeout_at.isoformat(),
            "ttl_seconds": decision.ttl_seconds or int((timeout_at - created_at).total_seconds()),
        }
    )
    if decision.reason_code is not None:
        diff["reason_code"] = (
            decision.reason_code.value
            if isinstance(decision.reason_code, ReasonCode)
            else str(decision.reason_code)
        )
    if decision.requires_uv:
        diff["requires_uv"] = True
    return EscalationRecord(
        escalation_id=escalation_id or str(uuid4()),
        purchase_id=purchase_id,
        mandate_id=mandate_id,
        intent=intent,
        offer=offer,
        status="pending",
        level=level.value if isinstance(level, EscalationLevel) else str(level),
        diff=diff,
        created_at=created_at,
        timeout_at=timeout_at,
    )


def domain_escalation(record: EscalationRecord) -> Escalation:
    return Escalation(
        level=record.level,
        created_at=record.created_at,
        timeout_at=record.timeout_at,
        diff=record.diff,
    )


def resolve_envelope(
    record: EscalationRecord,
    now: datetime,
    approval: str | bool,
    *,
    uv_verifier: Any = None,
    assertion: Any = None,
    uv_verified: bool | None = None,
) -> Decision:
    """Validate the human envelope; the caller must re-run the complete gate."""

    current = _utc(now)
    if record.status != "pending" or escalation_expired(record.timeout_at, current):
        return Decision(DecisionValue.REJECTED, ReasonCode.ESCALATION_TIMEOUT_DENIED)
    return resolve_domain_escalation(
        domain_escalation(record),
        current,
        approval,
        uv_verifier=uv_verifier,
        assertion=assertion,
        diff=record.diff,
        uv_verified=uv_verified,
    )


def mark_expired(record: EscalationRecord) -> EscalationRecord:
    return replace(record, status="expired", decision=ReasonCode.ESCALATION_TIMEOUT_DENIED.value)


__all__ = [
    "create_escalation",
    "domain_escalation",
    "mark_expired",
    "resolve_envelope",
]
