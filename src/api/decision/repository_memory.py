"""Thread-safe DEV2 fakes with the same semantics as the Postgres ports."""

from __future__ import annotations

import hmac
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Any
from uuid import uuid4

from src.api.domain.idempotency import (
    IDEMPOTENCY_TTL,
    IdempotencyConflict,
    IdempotencyRecord,
    derive_idempotency_key,
    make_record,
)
from src.api.domain.models import MandateStatus, Offer, SpendView, amount_decimal

from .ports import EscalationRecord, OutboxEvent, PurchaseRecord


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: Decimal | str | int) -> Decimal:
    return amount_decimal(value)


def _minute(value: datetime) -> datetime:
    current = _utc(value)
    return current.replace(second=0, microsecond=0)


class InMemoryVelocityStore:
    """A lock-protected velocity counter fake using minute buckets."""

    _OPEN_BUCKET = datetime(1970, 1, 1, tzinfo=UTC)

    def __init__(self) -> None:
        self._lock = RLock()
        self._buckets: dict[tuple[str, str, str, datetime], Decimal] = {}
        self._cooldowns: dict[str, datetime] = {}

    def _add(
        self, mandate_id: str, counter: str, window: str, bucket: datetime, value: Decimal
    ) -> None:
        key = (mandate_id, counter, window, bucket)
        self._buckets[key] = self._buckets.get(key, Decimal("0")) + value

    def _sum(
        self, mandate_id: str, counter: str, window: str, start: datetime, end: datetime
    ) -> Decimal:
        return sum(
            (
                value
                for (
                    stored_mandate,
                    stored_counter,
                    stored_window,
                    bucket,
                ), value in self._buckets.items()
                if stored_mandate == mandate_id
                and stored_counter == counter
                and stored_window == window
                and start <= bucket <= end
            ),
            Decimal("0"),
        )

    def increment_intent(
        self,
        mandate_id: str,
        amount: Decimal | str,
        now: datetime,
        *,
        transaction: Any = None,
    ) -> None:
        del transaction
        bucket = _minute(now)
        with self._lock:
            self._add(mandate_id, "intents", "1m", bucket, Decimal("1"))
            self._add(mandate_id, "amount_sum", "1m", bucket, _decimal(amount))

    record_intent = increment_intent

    def increment_escalation(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        del transaction
        with self._lock:
            self._add(mandate_id, "escalations", "1h", _minute(now), Decimal("1"))

    record_escalation = increment_escalation

    def increment_open_authorizations(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        del transaction
        _utc(now)
        with self._lock:
            self._add(mandate_id, "open_authz", "current", self._OPEN_BUCKET, Decimal("1"))

    open_authorization = increment_open_authorizations

    def decrement_open_authorizations(
        self, mandate_id: str, now: datetime, *, transaction: Any = None
    ) -> None:
        del transaction
        _utc(now)
        with self._lock:
            key = (mandate_id, "open_authz", "current", self._OPEN_BUCKET)
            self._buckets[key] = max(Decimal("0"), self._buckets.get(key, Decimal("0")) - 1)

    release_authorization = decrement_open_authorizations

    def record_cooldown(
        self, mandate_id: str, expires_at: datetime, *, transaction: Any = None
    ) -> None:
        del transaction
        expiry = _utc(expires_at)
        with self._lock:
            if expiry > self._cooldowns.get(mandate_id, datetime.min.replace(tzinfo=UTC)):
                self._cooldowns[mandate_id] = expiry

    set_cooldown = record_cooldown

    def get_cooldown(self, mandate_id: str, now: datetime) -> datetime | None:
        current = _utc(now)
        with self._lock:
            expiry = self._cooldowns.get(mandate_id)
            if expiry is None:
                return None
            if current >= expiry:
                self._cooldowns.pop(mandate_id, None)
                return None
            return expiry

    def cooldown_until(self, mandate_id: str, now: datetime) -> datetime | None:
        return self.get_cooldown(mandate_id, now)

    def count_intents(
        self, mandate_id: str, now: datetime, window: timedelta = timedelta(seconds=60)
    ) -> int:
        current = _utc(now)
        start = _minute(current - window)
        with self._lock:
            return int(self._sum(mandate_id, "intents", "1m", start, _minute(current)))

    def count_escalations(self, mandate_id: str, now: datetime) -> int:
        current = _utc(now)
        start = _minute(current - timedelta(hours=1))
        with self._lock:
            return int(self._sum(mandate_id, "escalations", "1h", start, _minute(current)))

    def amount_sum(self, mandate_id: str, now: datetime) -> Decimal:
        current = _utc(now)
        start = _minute(current - timedelta(seconds=60))
        with self._lock:
            return self._sum(mandate_id, "amount_sum", "1m", start, _minute(current))

    def open_authorizations(self, mandate_id: str, now: datetime | None = None) -> int:
        if now is not None:
            _utc(now)
        with self._lock:
            value = self._buckets.get(
                (mandate_id, "open_authz", "current", self._OPEN_BUCKET), Decimal("0")
            )
            return max(0, int(value))

    def get_spend_view(
        self,
        mandate_id: str,
        now: datetime,
        *,
        spent_total: Decimal | str | int = Decimal("0.00"),
        reserved_total: Decimal | str | int = Decimal("0.00"),
        txn_count_period: int = 0,
        mandate_status: MandateStatus | str = MandateStatus.ACTIVE,
    ) -> SpendView:
        return SpendView(
            spent_total=spent_total,
            reserved_total=reserved_total,
            txn_count_period=txn_count_period,
            mandate_status=mandate_status,
            intents_last_60s=self.count_intents(mandate_id, now),
            escalations_last_hour=self.count_escalations(mandate_id, now),
            open_authorizations=self.open_authorizations(mandate_id, now),
            cooldown_until=self.get_cooldown(mandate_id, now),
        )

    spend_view = get_spend_view
    read = get_spend_view


class InMemoryIdempotencyStore:
    """A replay-safe in-memory implementation of ``idempotency_keys``."""

    def __init__(self, secret: str | bytes = "local-development-only") -> None:
        self.secret = secret
        self._lock = RLock()
        self._records: dict[str, IdempotencyRecord] = {}

    def reserve(self, record: IdempotencyRecord, now: datetime | None = None) -> IdempotencyRecord:
        current = _utc(now) if now is not None else datetime.now(UTC)
        expected_key = derive_idempotency_key(record.derived_from, self.secret)
        if not hmac.compare_digest(record.key, expected_key):
            raise IdempotencyConflict("idempotency key must be derived from the source jti")
        candidate = record
        with self._lock:
            existing = self._records.get(candidate.key)
            if existing is None or existing.is_expired(current):
                self._records[candidate.key] = candidate
                return candidate
            if (
                existing.scope != candidate.scope
                or existing.derived_from != candidate.derived_from
                or not hmac.compare_digest(
                    existing.request_fingerprint, candidate.request_fingerprint
                )
            ):
                raise IdempotencyConflict("same idempotency key used with a different request")
            return existing

    def reserve_for(
        self,
        jti: str,
        scope: str,
        request: Any,
        created_at: datetime,
        *,
        ttl: timedelta = IDEMPOTENCY_TTL,
    ) -> IdempotencyRecord:
        return self.reserve(
            make_record(
                jti,
                self.secret,
                scope,
                request,
                created_at,
                ttl=ttl,
                claim_token=uuid4().hex,
            ),
            created_at,
        )

    def get(self, key: str, now: datetime | None = None) -> IdempotencyRecord | None:
        current = _utc(now) if now is not None else datetime.now(UTC)
        with self._lock:
            record = self._records.get(key)
            if record is not None and record.is_expired(current):
                self._records.pop(key, None)
                return None
            return record

    def save_response(
        self,
        key: str,
        response: Mapping[str, Any],
        now: datetime | None = None,
    ) -> IdempotencyRecord:
        current = _utc(now) if now is not None else datetime.now(UTC)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                raise KeyError(key)
            if record.is_expired(current):
                self._records.pop(key, None)
                raise KeyError(key)
            if record.response is not None:
                return record
            updated = replace(record, response=dict(response))
            self._records[key] = updated
            return updated

    def purge_expired(self, now: datetime) -> int:
        current = _utc(now)
        with self._lock:
            expired = [key for key, record in self._records.items() if record.is_expired(current)]
            for key in expired:
                self._records.pop(key, None)
            return len(expired)

    @property
    def records(self) -> dict[str, IdempotencyRecord]:
        with self._lock:
            return dict(self._records)


class InMemoryMandateReader:
    def __init__(self) -> None:
        self._mandates: dict[str, Any] = {}

    def put(self, mandate: Any, *, jti: str | None = None) -> Any:
        key = jti or (mandate.get("jti") if isinstance(mandate, Mapping) else mandate.jti)
        self._mandates[str(key)] = mandate
        return mandate

    def get_by_jti(self, jti: str) -> Any | None:
        return self._mandates.get(jti)

    get_mandate_by_jti = get_by_jti


class InMemoryOfferCatalog:
    def __init__(self) -> None:
        self._offers: dict[str, Any] = {}

    def put(self, offer: Offer | Mapping[str, Any], *, offer_id: str | None = None) -> Any:
        key = offer_id or (offer.get("offer_id") if isinstance(offer, Mapping) else offer.offer_id)
        self._offers[str(key)] = offer
        return offer

    def get(self, offer_id: str) -> Any | None:
        return self._offers.get(offer_id)


class InMemoryPurchaseStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.records: dict[str, PurchaseRecord] = {}

    def create(self, purchase: PurchaseRecord) -> PurchaseRecord:
        with self._lock:
            if purchase.purchase_id in self.records:
                return self.records[purchase.purchase_id]
            self.records[purchase.purchase_id] = purchase
            return purchase

    save = create

    def update(self, purchase_id: str, **changes: Any) -> PurchaseRecord | None:
        with self._lock:
            current = self.records.get(purchase_id)
            if current is None:
                return None
            updated = replace(current, **changes, updated_at=datetime.now(UTC))
            self.records[purchase_id] = updated
            return updated

    def get(self, purchase_id: str) -> PurchaseRecord | None:
        return self.records.get(purchase_id)

    def get_by_intent(self, intent_jti: str) -> PurchaseRecord | None:
        return next((item for item in self.records.values() if item.intent_jti == intent_jti), None)


class InMemoryEscalationStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.records: dict[str, EscalationRecord] = {}

    def create(self, escalation: EscalationRecord) -> EscalationRecord:
        with self._lock:
            self.records.setdefault(escalation.escalation_id, escalation)
            return self.records[escalation.escalation_id]

    save = create

    def get(self, escalation_id: str) -> EscalationRecord | None:
        return self.records.get(escalation_id)

    def update(self, escalation_id: str, **changes: Any) -> EscalationRecord | None:
        with self._lock:
            current = self.records.get(escalation_id)
            if current is None:
                return None
            updated = replace(current, **changes)
            self.records[escalation_id] = updated
            return updated


class InMemoryOutboxWriter:
    def __init__(self) -> None:
        self._lock = RLock()
        self.events: list[OutboxEvent] = []
        self.fail = False

    def append(self, event: OutboxEvent, *, transaction: Any = None) -> None:
        del transaction
        if self.fail:
            raise RuntimeError("outbox is unavailable")
        with self._lock:
            if any(item.event_id == event.event_id for item in self.events):
                return
            self.events.append(event)

    write = append


class InMemoryReservationStore:
    """Atomic conditional reservation fake for verify-path tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._mandates: dict[str, dict[str, Any]] = {}
        self._reservations: dict[str, tuple[str, Decimal]] = {}
        self._reservation_keys: dict[str, str] = {}

    def register_mandate(
        self,
        mandate_id: str,
        *,
        total_budget: Decimal | str | int,
        spent_total: Decimal | str | int = Decimal("0.00"),
        reserved_total: Decimal | str | int = Decimal("0.00"),
        txn_count_period: int = 0,
        status: str = "active",
    ) -> None:
        with self._lock:
            self._mandates[mandate_id] = {
                "total_budget": _decimal(total_budget),
                "spent_total": _decimal(spent_total),
                "reserved_total": _decimal(reserved_total),
                "txn_count_period": txn_count_period,
                "status": status,
            }

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
        del transaction
        candidate = _decimal(amount)
        budget = _decimal(total_budget)
        with self._lock:
            if reservation_key is not None:
                existing_id = self._reservation_keys.get(reservation_key)
                if existing_id is not None:
                    existing = self._reservations.get(existing_id)
                    if (
                        existing is not None
                        and existing[0] == mandate_id
                        and existing[1] == candidate
                    ):
                        return existing_id
                    return None
            state = self._mandates.setdefault(
                mandate_id,
                {
                    "total_budget": budget,
                    "spent_total": Decimal("0.00"),
                    "reserved_total": Decimal("0.00"),
                    "txn_count_period": 0,
                    "status": "active",
                },
            )
            if state["status"] != "active":
                return None
            if state["spent_total"] + state["reserved_total"] + candidate > state["total_budget"]:
                return None
            if max_txn_count is not None and state["txn_count_period"] + 1 > max_txn_count:
                return None
            identifier = reservation_id or str(uuid4())
            if identifier in self._reservations:
                existing = self._reservations[identifier]
                if existing == (mandate_id, candidate):
                    return identifier
                return None
            state["reserved_total"] += candidate
            state["txn_count_period"] += 1
            self._reservations[identifier] = (mandate_id, candidate)
            if reservation_key is not None:
                self._reservation_keys[reservation_key] = identifier
            return identifier

    def release(
        self,
        mandate_id: str,
        amount: Decimal | str,
        reservation_id: str | None = None,
        *,
        transaction: Any = None,
    ) -> bool:
        del transaction
        candidate = _decimal(amount)
        with self._lock:
            if reservation_id is not None:
                stored = self._reservations.pop(reservation_id, None)
                if stored is None or stored[0] != mandate_id:
                    return False
                candidate = stored[1]
                for key, identifier in tuple(self._reservation_keys.items()):
                    if identifier == reservation_id:
                        self._reservation_keys.pop(key, None)
            state = self._mandates.get(mandate_id)
            if state is None or state["reserved_total"] < candidate:
                return False
            state["reserved_total"] -= candidate
            state["txn_count_period"] = max(0, state["txn_count_period"] - 1)
            return True

    @property
    def mandates(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._mandates.items()}


class NoopTransactionManager:
    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None


MemoryVelocityStore = InMemoryVelocityStore
MemoryIdempotencyStore = InMemoryIdempotencyStore
VelocityStoreMemory = InMemoryVelocityStore
IdempotencyStoreMemory = InMemoryIdempotencyStore
MemoryEscalationStore = InMemoryEscalationStore
MemoryMandateReader = InMemoryMandateReader
MemoryOfferCatalog = InMemoryOfferCatalog
MemoryOutboxWriter = InMemoryOutboxWriter
MemoryPurchaseStore = InMemoryPurchaseStore
MemoryMandateRepository = InMemoryMandateReader
MemoryOfferRepository = InMemoryOfferCatalog
MemoryReservationStore = InMemoryReservationStore


__all__ = [
    "IdempotencyStoreMemory",
    "InMemoryEscalationStore",
    "InMemoryIdempotencyStore",
    "InMemoryMandateReader",
    "InMemoryOfferCatalog",
    "InMemoryOutboxWriter",
    "InMemoryPurchaseStore",
    "InMemoryReservationStore",
    "InMemoryVelocityStore",
    "MemoryEscalationStore",
    "MemoryIdempotencyStore",
    "MemoryMandateReader",
    "MemoryMandateRepository",
    "MemoryOfferCatalog",
    "MemoryOfferRepository",
    "MemoryOutboxWriter",
    "MemoryPurchaseStore",
    "MemoryReservationStore",
    "MemoryVelocityStore",
    "NoopTransactionManager",
    "VelocityStoreMemory",
]
