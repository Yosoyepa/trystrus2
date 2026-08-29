"""Persistence boundary.

The first demo implementation uses an in-memory repository. Its methods return
domain records rather than database rows, so replacing it with PostgreSQL does
not alter the service or HTTP layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from typing import Any


@dataclass
class MandateRecord:
    id: str
    jti: str
    user_id: str
    agent_id: str
    status: str = "active"
    claims: dict[str, Any] = field(default_factory=dict)
    sd_jwt: str = ""
    payment_method_ref: str = ""
    reserved_amount: Decimal = Decimal("0.00")
    spent_total: Decimal = Decimal("0.00")
    txn_count_period: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PurchaseRecord:
    id: str
    mandate_id: str
    intent_jti: str
    status: str
    reason_code: str | None = None
    reservation_id: str | None = None
    receipt: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class EscalationRecord:
    id: str
    purchase_id: str
    mandate_id: str
    status: str
    level: str
    diff: dict[str, Any]
    timeout_at: datetime
    decision: str | None = None
    approver: str | None = None
    channel: str | None = None
    receipt_sig: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryRepository:
    """Thread-safe repository fake used by local runs and tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.mandates: dict[str, MandateRecord] = {}
        self.mandates_by_jti: dict[str, str] = {}
        self.purchases: dict[str, PurchaseRecord] = {}
        self.purchases_by_intent: dict[str, str] = {}
        self.escalations: dict[str, EscalationRecord] = {}
        self.offers: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []
        self.webhook_archive: list[dict[str, Any]] = []
        self.ceremony_logs: dict[str, dict[str, Any]] = {}

    def save_mandate(self, mandate: MandateRecord) -> MandateRecord:
        with self._lock:
            self.mandates[mandate.id] = mandate
            self.mandates_by_jti[mandate.jti] = mandate.id
        return mandate

    def get_mandate(self, mandate_id: str) -> MandateRecord | None:
        return self.mandates.get(mandate_id)

    def get_mandate_by_jti(self, jti: str) -> MandateRecord | None:
        mandate_id = self.mandates_by_jti.get(jti)
        return self.mandates.get(mandate_id) if mandate_id else None

    def save_purchase(self, purchase: PurchaseRecord) -> PurchaseRecord:
        with self._lock:
            self.purchases[purchase.id] = purchase
            self.purchases_by_intent[purchase.intent_jti] = purchase.id
        return purchase

    def get_purchase(self, purchase_id: str) -> PurchaseRecord | None:
        return self.purchases.get(purchase_id)

    def get_purchase_by_intent(self, intent_jti: str) -> PurchaseRecord | None:
        purchase_id = self.purchases_by_intent.get(intent_jti)
        return self.purchases.get(purchase_id) if purchase_id else None

    def save_escalation(self, escalation: EscalationRecord) -> EscalationRecord:
        with self._lock:
            self.escalations[escalation.id] = escalation
        return escalation

    def get_escalation(self, escalation_id: str) -> EscalationRecord | None:
        return self.escalations.get(escalation_id)

    def active_escalations(self, mandate_id: str) -> list[EscalationRecord]:
        return [
            item
            for item in self.escalations.values()
            if item.mandate_id == mandate_id and item.status == "pending"
        ]

    def append_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.audit_events.append(dict(event))

    def add_webhook(self, item: dict[str, Any]) -> None:
        with self._lock:
            self.webhook_archive.append(dict(item))

    def has_webhook_key(self, key: str) -> bool:
        return any(item.get("event_key") == key for item in self.webhook_archive)

    def spend_for(self, mandate: MandateRecord) -> tuple[Decimal, Decimal, int]:
        return mandate.spent_total, mandate.reserved_amount, mandate.txn_count_period
