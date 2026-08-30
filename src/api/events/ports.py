"""Ports and data models for event distribution and outbox relay (decision #10)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


def ensure_aware_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC and ensure it is timezone-aware."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("event timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Outbox event matching the frozen envelope.

    Envelope: `{event_id, type, aggregate_id, payload, created_at}`.
    """

    event_id: str
    type: str
    aggregate_id: str
    payload: Mapping[str, Any]
    created_at: datetime
    seq: int | None = None
    relayed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id cannot be empty")
        if not self.type:
            raise ValueError("type cannot be empty")
        if not self.aggregate_id:
            raise ValueError("aggregate_id cannot be empty")
        created_at = ensure_aware_utc(self.created_at)
        object.__setattr__(self, "created_at", created_at)
        if self.relayed_at is not None:
            object.__setattr__(self, "relayed_at", ensure_aware_utc(self.relayed_at))

    def to_dict(self) -> dict[str, Any]:
        """Convert to event envelope dictionary."""
        return {
            "event_id": self.event_id,
            "type": self.type,
            "aggregate_id": self.aggregate_id,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "relayed_at": (
                self.relayed_at.isoformat().replace("+00:00", "Z")
                if self.relayed_at
                else None
            ),
        }


class Sink(Protocol):
    """Destination port for relayed outbox events (e.g. SSE, BotNotifier, WebhookPoster)."""

    def handle(self, event: OutboxEvent) -> None:
        """Handle event delivery. Must be idempotent (deduplicate by event_id)."""
        ...


class OutboxStore(Protocol):
    """Persistence port for the outbox table."""

    def append(self, event: OutboxEvent) -> OutboxEvent:
        """Append an event to the outbox."""
        ...

    def fetch_unrelayed(self, limit: int = 100) -> Sequence[OutboxEvent]:
        """Fetch unrelayed events with tail/skip-locked concurrency control."""
        ...

    def mark_relayed(self, event_id: str, relayed_at: datetime) -> None:
        """Mark an event as successfully relayed."""
        ...


class Clock(Protocol):
    """Time provider protocol."""

    def now(self) -> datetime:
        ...


class SystemClock:
    """Default system clock in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


__all__ = [
    "Clock",
    "OutboxEvent",
    "OutboxStore",
    "Sink",
    "SystemClock",
    "ensure_aware_utc",
]
