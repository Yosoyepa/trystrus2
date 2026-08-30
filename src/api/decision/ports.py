"""Ports and small records for the DEV2 decision core.

The decision service depends on these interfaces rather than on an HTTP
framework or a database driver.  DEV3 can adapt its API repositories to the
ports without changing the deterministic domain gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from src.api.domain.idempotency import IdempotencyRecord
from src.api.domain.models import (
    MandateClaims,
    Offer,
    PurchaseIntent,
    SpendView,
)


@dataclass(frozen=True, slots=True)
class PurchaseRecord:
    """Persistence-neutral purchase projection used by the decision service."""

    purchase_id: str
    mandate_id: str
    intent_jti: str
    status: str
    reason_code: str | None = None
    reservation_id: str | None = None
    escalation_id: str | None = None
    receipt: Mapping[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class EscalationRecord:
    """Stored escalation envelope; the level remains in the JSON diff too."""

    escalation_id: str
    purchase_id: str
    mandate_id: str
    intent: PurchaseIntent | Mapping[str, Any]
    offer: Offer | Mapping[str, Any] | None
    status: str
    level: str
    diff: Mapping[str, Any]
    created_at: datetime
    timeout_at: datetime
    decision: str | None = None
    approver: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Canonical event envelope written in the business transaction."""

    event_id: str
    type: str
    aggregate_id: str
    payload: Mapping[str, Any]
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "aggregate_id": self.aggregate_id,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC timestamp."""


class MandateReader(Protocol):
    def get_by_jti(self, jti: str) -> MandateClaims | Mapping[str, Any] | None:
        """Load the signed mandate projection by its immutable JTI."""


class OfferCatalog(Protocol):
    def get(self, offer_id: str) -> Offer | Mapping[str, Any] | None:
        """Load the catalogue offer used by R-PRICE and scope checks."""


class VelocityStore(Protocol):
    def get_spend_view(self, mandate_id: str, now: datetime) -> SpendView:
        """Read the counters that existed before the candidate intent."""

    def increment_intent(
        self,
        mandate_id: str,
        amount: Decimal | str,
        now: datetime,
        *,
        transaction: Any = None,
    ) -> None:
        """Record one observed intent using an atomic store operation."""

    def increment_escalation(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        """Record one escalation in the rolling hour."""

    def increment_open_authorizations(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        """Record one authorization that is waiting for capture."""

    def decrement_open_authorizations(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        """Release one open authorization slot."""

    def record_cooldown(
        self, mandate_id: str, expires_at: datetime, *, transaction: Any = None
    ) -> None:
        """Persist a cooldown expiry without adding a schema column."""


class IdempotencyStore(Protocol):
    def reserve(self, record: IdempotencyRecord) -> IdempotencyRecord:
        """Atomically claim a derived key or validate an existing claim."""

    def get(self, key: str, now: datetime | None = None) -> IdempotencyRecord | None:
        """Read a non-expired record, lazily omitting expired rows."""

    def save_response(
        self,
        key: str,
        response: Mapping[str, Any],
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        """Persist the first response for a reserved key."""

    def purge_expired(self, now: datetime) -> int:
        """Delete expired records and return the number removed."""


class AtomicReservationStore(Protocol):
    def reserve(
        self,
        mandate_id: str,
        amount: Decimal | str,
        total_budget: Decimal | str,
        *,
        max_txn_count: int | None = None,
        reservation_id: str | None = None,
        reservation_key: str | None = None,
        transaction: Any = None,
    ) -> str | None:
        """Atomically reserve budget; ``None`` means the conditional update lost."""

    def release(
        self,
        mandate_id: str,
        amount: Decimal | str,
        reservation_id: str | None = None,
        *,
        transaction: Any = None,
    ) -> bool:
        """Compensate one reservation idempotently."""


class PurchaseStore(Protocol):
    def create(self, purchase: PurchaseRecord, *, transaction: Any = None) -> PurchaseRecord: ...

    def update(
        self, purchase_id: str, *, transaction: Any = None, **changes: Any
    ) -> PurchaseRecord | None: ...

    def get(self, purchase_id: str) -> PurchaseRecord | None: ...

    def get_by_intent(self, intent_jti: str) -> PurchaseRecord | None: ...


class EscalationStore(Protocol):
    def create(
        self, escalation: EscalationRecord, *, transaction: Any = None
    ) -> EscalationRecord: ...

    def get(self, escalation_id: str) -> EscalationRecord | None: ...

    def update(
        self, escalation_id: str, *, transaction: Any = None, **changes: Any
    ) -> EscalationRecord | None: ...


class OutboxWriter(Protocol):
    def append(self, event: OutboxEvent, *, transaction: Any = None) -> None:
        """Append an event in the same transaction as the business write."""


class TransactionManager(Protocol):
    def transaction(self) -> AbstractContextManager[Any]:
        """Return a context that commits or rolls back all enclosed writes."""


class CapturePort(Protocol):
    def capture(
        self, *, purchase_id: str, reservation_id: str, amount: Decimal, currency: str
    ) -> Any:
        """Optional downstream capture hook; payment rails remain DEV3-owned."""


__all__ = [
    "AtomicReservationStore",
    "CapturePort",
    "Clock",
    "EscalationRecord",
    "EscalationStore",
    "IdempotencyStore",
    "MandateReader",
    "OfferCatalog",
    "OutboxEvent",
    "OutboxWriter",
    "PurchaseRecord",
    "PurchaseStore",
    "TransactionManager",
    "VelocityStore",
]
