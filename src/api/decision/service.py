"""DEV2 verify use case: deterministic gate, reservation, and compensation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.api.domain.idempotency import IdempotencyConflict, IdempotencyRecord
from src.api.domain.models import (
    Decision,
    DecisionValue,
    MandateStatus,
    PurchaseIntent,
    ReasonCode,
    SpendView,
)
from src.api.domain.policy import PolicyGate

from .escalation_flow import create_escalation, mark_expired, resolve_envelope
from .idempotency import claim_idempotency
from .ports import (
    EscalationRecord,
    OutboxEvent,
    PurchaseRecord,
)
from .reservation import ReservationRejected


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Decision plus durable identifiers returned to an API adapter."""

    decision: Decision
    purchase_id: str
    status: str
    escalation_id: str | None = None

    @property
    def is_approved(self) -> bool:
        return self.decision.is_approved

    @property
    def is_rejected(self) -> bool:
        return self.decision.is_rejected

    @property
    def is_escalated(self) -> bool:
        return self.decision.is_escalated

    @property
    def reason_code(self) -> ReasonCode | str | None:
        return self.decision.reason_code

    @property
    def reservation_id(self) -> str | None:
        return self.decision.reservation_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.decision.value,
            "reason_code": self.decision.reason_code.value
            if isinstance(self.decision.reason_code, ReasonCode)
            else self.decision.reason_code,
            "reservation_id": self.decision.reservation_id,
            "purchase_id": self.purchase_id,
            "status": self.status,
            "escalation_id": self.escalation_id,
            "diff": dict(self.decision.diff or {}),
            "level": self.decision.level.value
            if hasattr(self.decision.level, "value")
            else self.decision.level,
            "ttl_seconds": self.decision.ttl_seconds,
            "requires_uv": self.decision.requires_uv,
        }


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _mandate_id(mandate: Any, fallback: str) -> str:
    return str(_value(mandate, "id", _value(mandate, "jti", fallback)))


def _intent_jti(intent: Any) -> str:
    return str(_value(intent, "jti", "unknown-intent"))


def _status(mandate: Any) -> MandateStatus | str:
    return _value(mandate, "status", MandateStatus.ACTIVE)


def _canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


class DecisionService:
    """Orchestrate DEV2 side effects around the pure policy gate.

    The constructor deliberately accepts protocols rather than concrete
    repositories.  HTTP wiring and payment rails remain outside this class.
    """

    def __init__(
        self,
        *,
        gate: Any | None = None,
        policy_gate: Any | None = None,
        mandate_reader: Any | None = None,
        mandates: Any | None = None,
        offer_catalog: Any | None = None,
        offers: Any | None = None,
        velocity_store: Any | None = None,
        reservation_store: Any | None = None,
        reservation: Any | None = None,
        purchase_store: Any | None = None,
        purchases: Any | None = None,
        escalation_store: Any | None = None,
        escalations: Any | None = None,
        outbox: Any | None = None,
        transaction_manager: Any | None = None,
        clock: Any | None = None,
        uv_verifier: Any | None = None,
        idempotency_store: Any | None = None,
        idem_store: Any | None = None,
        idempotency_secret: str | bytes = "local-development-only",
        idempotency_scope: str = "verify",
    ) -> None:
        self.gate = gate or policy_gate or PolicyGate()
        self.mandate_reader = mandate_reader or mandates
        self.offer_catalog = offer_catalog or offers
        self.velocity_store = velocity_store
        self.reservation_store = reservation_store or reservation
        self.purchase_store = purchase_store or purchases
        self.escalation_store = escalation_store or escalations
        self.outbox = outbox
        self.transaction_manager = transaction_manager
        self.clock = clock
        self.uv_verifier = uv_verifier
        self.idempotency_store = idempotency_store or idem_store
        self.idempotency_secret = idempotency_secret
        self.idempotency_scope = idempotency_scope

    def _now(self, now: datetime | None) -> datetime:
        value = now
        if value is None:
            value = self.clock.now() if self.clock is not None else datetime.now(UTC)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def _transaction(self) -> Any:
        if self.transaction_manager is None:
            return nullcontext(None)
        transaction = getattr(self.transaction_manager, "transaction", None)
        if transaction is None:
            return nullcontext(self.transaction_manager)
        context = transaction() if callable(transaction) else transaction
        return context

    def _read_mandate(self, jti: str) -> Any | None:
        reader = self.mandate_reader
        if reader is None:
            return None
        for name in ("get_by_jti", "get_mandate_by_jti", "get"):
            method = getattr(reader, name, None)
            if method is not None:
                return method(jti)
        if callable(reader):
            return reader(jti)
        return None

    def _read_offer(self, offer_id: str) -> Any | None:
        catalog = self.offer_catalog
        if catalog is None:
            return None
        for name in ("get", "get_offer", "find"):
            method = getattr(catalog, name, None)
            if method is not None:
                return method(offer_id)
        if callable(catalog):
            return catalog(offer_id)
        return None

    def _spend(self, mandate_id: str, mandate: Any, now: datetime) -> SpendView:
        store = self.velocity_store
        spent_total = _value(mandate, "spent_total", _value(mandate, "spent", "0.00"))
        reserved_total = _value(
            mandate, "reserved_total", _value(mandate, "reserved_amount", "0.00")
        )
        txn_count = _value(mandate, "txn_count_period", 0)
        mandate_status = _status(mandate)
        if store is None:
            return SpendView(
                spent_total=spent_total,
                reserved_total=reserved_total,
                txn_count_period=txn_count,
                mandate_status=mandate_status,
            )
        for name in ("get_spend_view", "spend_view", "read"):
            method = getattr(store, name, None)
            if method is None:
                continue
            try:
                return method(
                    mandate_id,
                    now,
                    spent_total=spent_total,
                    reserved_total=reserved_total,
                    txn_count_period=txn_count,
                    mandate_status=mandate_status,
                )
            except TypeError:
                return method(mandate_id, now)
        return SpendView(
            spent_total=spent_total,
            reserved_total=reserved_total,
            txn_count_period=txn_count,
            mandate_status=mandate_status,
        )

    def _evaluate(
        self,
        mandate: Any,
        intent: Any,
        spend: SpendView,
        now: datetime,
        offer: Any,
        *,
        approved_stepup: bool = False,
    ) -> Decision:
        try:
            return self.gate.evaluate(
                mandate,
                intent,
                spend,
                now,
                offer,
                approved_stepup=approved_stepup,
            )
        except TypeError:
            try:
                return self.gate.evaluate(mandate, intent, spend, now, offer)
            except TypeError:
                return self.gate.evaluate(mandate, intent, spend, now)

    def _purchase_id(self, intent: Any, purchase_id: str | None) -> str:
        return purchase_id or f"purchase-{_intent_jti(intent)}"

    def _new_event(
        self, event_type: str, aggregate_id: str, payload: Mapping[str, Any], now: datetime
    ) -> OutboxEvent:
        digest = hashlib.sha256(_canonical_payload(payload).encode("utf-8")).hexdigest()[:24]
        return OutboxEvent(
            event_id=f"{event_type}:{aggregate_id}:{digest}",
            type=event_type,
            aggregate_id=aggregate_id,
            payload=dict(payload),
            created_at=now,
        )

    def _emit(
        self,
        event_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        now: datetime,
        transaction: Any = None,
    ) -> None:
        if self.outbox is None:
            return
        event = self._new_event(event_type, aggregate_id, payload, now)
        append = getattr(self.outbox, "append", None) or getattr(self.outbox, "write", None)
        if append is None:
            raise RuntimeError("outbox writer does not expose append")
        try:
            if transaction is None:
                append(event)
            else:
                try:
                    append(event, transaction=transaction)
                except TypeError:
                    append(event)
        except TypeError:
            append(event.as_dict())

    def _save_purchase(self, purchase: PurchaseRecord, transaction: Any = None) -> PurchaseRecord:
        if self.purchase_store is None:
            return purchase
        method = getattr(self.purchase_store, "create", None) or getattr(
            self.purchase_store, "save", None
        )
        if method is None:
            raise RuntimeError("purchase store does not expose create")
        if transaction is None:
            result = method(purchase)
        else:
            try:
                result = method(purchase, transaction=transaction)
            except TypeError:
                result = method(purchase)
        return result or purchase

    def _update_purchase(
        self, purchase_id: str, *, transaction: Any = None, **changes: Any
    ) -> PurchaseRecord | None:
        if self.purchase_store is None:
            return None
        method = getattr(self.purchase_store, "update", None)
        if method is None:
            raise RuntimeError("purchase store does not expose update")
        if transaction is None:
            return method(purchase_id, **changes)
        try:
            return method(purchase_id, transaction=transaction, **changes)
        except TypeError:
            return method(purchase_id, **changes)

    def _read_purchase_by_intent(self, intent_jti: str) -> PurchaseRecord | None:
        if self.purchase_store is None:
            return None
        method = getattr(self.purchase_store, "get_by_intent", None)
        return method(intent_jti) if method is not None else None

    def _read_purchase(self, purchase_id: str) -> PurchaseRecord | None:
        if self.purchase_store is None:
            return None
        method = getattr(self.purchase_store, "get", None)
        return method(purchase_id) if method is not None else None

    def _claim_idempotency(
        self,
        intent: Any,
        now: datetime,
        *,
        request: Any = None,
        scope: str | None = None,
    ) -> tuple[IdempotencyRecord | None, bool]:
        store = self.idempotency_store
        jti = _intent_jti(intent)
        if store is None or not jti or jti == "unknown-intent":
            return None, True
        operation_scope = scope or self.idempotency_scope
        body = intent if request is None else request
        claim = claim_idempotency(
            store,
            jti=jti,
            secret=self.idempotency_secret,
            scope=operation_scope,
            request=body,
            now=now,
        )
        return claim.record, claim.owns_claim

    def _save_idempotency(
        self,
        record: IdempotencyRecord | None,
        result: VerificationResult,
        now: datetime | None = None,
    ) -> VerificationResult:
        if record is None or self.idempotency_store is None:
            return result
        if record.response is not None:
            return result
        save = getattr(self.idempotency_store, "save_response", None)
        if save is None:
            return result
        if now is None:
            save(record.key, result.as_dict())
        else:
            try:
                save(record.key, result.as_dict(), now=now)
            except TypeError:
                save(record.key, result.as_dict())
        return result

    @staticmethod
    def _result_from_idempotency(
        response: Mapping[str, Any],
        fallback_purchase_id: str,
    ) -> VerificationResult:
        try:
            decision = Decision(
                response.get("decision", DecisionValue.REJECTED),
                response.get("reason_code"),
                reservation_id=response.get("reservation_id"),
                diff=response.get("diff"),
                level=response.get("level"),
                ttl_seconds=response.get("ttl_seconds"),
                requires_uv=bool(response.get("requires_uv", False)),
            )
        except (TypeError, ValueError):
            decision = Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED)
        return VerificationResult(
            decision,
            str(response.get("purchase_id", fallback_purchase_id)),
            str(response.get("status", "rejected")),
            response.get("escalation_id"),
        )

    def _result_for_existing_purchase(self, purchase: PurchaseRecord) -> VerificationResult:
        try:
            reason = ReasonCode(purchase.reason_code) if purchase.reason_code else None
        except ValueError:
            reason = ReasonCode.MANDATE_SUSPENDED
        if purchase.status == "pending_capture":
            decision = Decision(
                DecisionValue.APPROVED,
                reservation_id=purchase.reservation_id,
            )
            return VerificationResult(decision, purchase.purchase_id, purchase.status)
        if purchase.status == "awaiting_escalation":
            escalation = (
                self._read_escalation(purchase.escalation_id) if purchase.escalation_id else None
            )
            level = escalation.level if escalation is not None else None
            ttl_seconds = escalation.diff.get("ttl_seconds") if escalation is not None else None
            requires_uv = bool(escalation and escalation.diff.get("requires_uv"))
            decision = Decision(
                DecisionValue.ESCALATED,
                reason,
                level=level,
                ttl_seconds=ttl_seconds,
                requires_uv=requires_uv,
                diff=escalation.diff if escalation is not None else None,
            )
            return VerificationResult(
                decision,
                purchase.purchase_id,
                purchase.status,
                purchase.escalation_id,
            )
        decision = Decision(
            DecisionValue.REJECTED,
            reason or ReasonCode.MANDATE_SUSPENDED,
        )
        return VerificationResult(decision, purchase.purchase_id, purchase.status)

    def _save_escalation(
        self, escalation: EscalationRecord, transaction: Any = None
    ) -> EscalationRecord:
        if self.escalation_store is None:
            return escalation
        method = getattr(self.escalation_store, "create", None) or getattr(
            self.escalation_store, "save", None
        )
        if method is None:
            raise RuntimeError("escalation store does not expose create")
        if transaction is None:
            result = method(escalation)
        else:
            try:
                result = method(escalation, transaction=transaction)
            except TypeError:
                result = method(escalation)
        return result or escalation

    def _read_escalation(self, escalation_id: str) -> EscalationRecord | None:
        if self.escalation_store is None:
            return None
        method = getattr(self.escalation_store, "get", None) or getattr(
            self.escalation_store, "get_by_id", None
        )
        return method(escalation_id) if method is not None else None

    def _update_escalation(
        self, escalation_id: str, *, transaction: Any = None, **changes: Any
    ) -> EscalationRecord | None:
        if self.escalation_store is None:
            return None
        method = getattr(self.escalation_store, "update", None)
        if method is None:
            raise RuntimeError("escalation store does not expose update")
        if transaction is None:
            return method(escalation_id, **changes)
        try:
            return method(escalation_id, transaction=transaction, **changes)
        except TypeError:
            return method(escalation_id, **changes)

    def _observe_intent(
        self, mandate_id: str, intent: Any, now: datetime, transaction: Any = None
    ) -> None:
        if self.velocity_store is None:
            return
        method = getattr(self.velocity_store, "increment_intent", None) or getattr(
            self.velocity_store, "record_intent", None
        )
        if method is not None:
            if transaction is None:
                method(mandate_id, _value(intent, "amount", "0.00"), now)
            else:
                try:
                    method(
                        mandate_id,
                        _value(intent, "amount", "0.00"),
                        now,
                        transaction=transaction,
                    )
                except TypeError:
                    method(mandate_id, _value(intent, "amount", "0.00"), now)

    def _observe_escalation(
        self,
        mandate_id: str,
        now: datetime,
        decision: Decision,
        transaction: Any = None,
    ) -> None:
        if self.velocity_store is None:
            return
        method = getattr(self.velocity_store, "increment_escalation", None) or getattr(
            self.velocity_store, "record_escalation", None
        )
        if method is not None:
            if transaction is None:
                method(mandate_id, now)
            else:
                try:
                    method(mandate_id, now, transaction=transaction)
                except TypeError:
                    method(mandate_id, now)
        cooldown = (decision.diff or {}).get("cooldown_until")
        if cooldown is not None:
            if isinstance(cooldown, str):
                cooldown = datetime.fromisoformat(cooldown.replace("Z", "+00:00"))
            set_cooldown = getattr(self.velocity_store, "record_cooldown", None) or getattr(
                self.velocity_store, "set_cooldown", None
            )
            if set_cooldown is not None:
                if transaction is None:
                    set_cooldown(mandate_id, cooldown)
                else:
                    try:
                        set_cooldown(mandate_id, cooldown, transaction=transaction)
                    except TypeError:
                        set_cooldown(mandate_id, cooldown)

    def _observe_open_authorization(
        self, mandate_id: str, now: datetime, transaction: Any = None
    ) -> None:
        if self.velocity_store is None:
            return
        method = getattr(self.velocity_store, "increment_open_authorizations", None) or getattr(
            self.velocity_store, "open_authorization", None
        )
        if method is not None:
            if transaction is None:
                method(mandate_id, now)
            else:
                try:
                    method(mandate_id, now, transaction=transaction)
                except TypeError:
                    method(mandate_id, now)

    def _release_open_authorization(
        self, mandate_id: str, now: datetime, transaction: Any = None
    ) -> None:
        if self.velocity_store is None:
            return
        method = getattr(self.velocity_store, "decrement_open_authorizations", None) or getattr(
            self.velocity_store, "release_authorization", None
        )
        if method is not None:
            if transaction is None:
                method(mandate_id, now)
            else:
                try:
                    method(mandate_id, now, transaction=transaction)
                except TypeError:
                    method(mandate_id, now)

    def _reserve(
        self,
        mandate: Any,
        mandate_id: str,
        intent: Any,
        purchase_id: str,
        transaction: Any,
    ) -> str:
        if self.reservation_store is None:
            raise ReservationRejected("no atomic reservation store configured")
        limits = _value(mandate, "limits")
        total_budget = _value(limits, "total_budget") if limits is not None else "0.00"
        max_txn = (
            _value(_value(limits, "max_txn"), "count")
            if _value(limits, "max_txn") is not None
            else None
        )
        method = getattr(self.reservation_store, "reserve", None)
        if method is None:
            raise ReservationRejected("reservation store does not expose reserve")
        amount = _value(intent, "amount")
        stable_reservation_id = f"reservation:{purchase_id}"
        try:
            result = method(
                mandate_id,
                amount,
                total_budget,
                max_txn_count=max_txn,
                reservation_id=stable_reservation_id,
                reservation_key=purchase_id,
                transaction=transaction,
            )
        except TypeError:
            try:
                result = method(
                    mandate_id,
                    amount,
                    total_budget,
                    max_txn_count=max_txn,
                    reservation_id=stable_reservation_id,
                    transaction=transaction,
                )
            except TypeError:
                result = method(mandate_id, amount, total_budget)
        if not result:
            raise ReservationRejected("atomic reservation condition was rejected")
        return str(getattr(result, "reservation_id", result))

    def _release(
        self,
        mandate_id: str,
        amount: Any,
        reservation_id: str | None,
        now: datetime,
        transaction: Any = None,
    ) -> bool:
        if self.reservation_store is None:
            return False
        method = getattr(self.reservation_store, "release", None)
        if method is None:
            return False
        try:
            return bool(method(mandate_id, amount, reservation_id, transaction=transaction))
        except TypeError:
            return bool(method(mandate_id, amount))

    def _rejected(
        self,
        *,
        intent: Any,
        mandate_id: str,
        purchase_id: str,
        decision: Decision,
        now: datetime,
    ) -> VerificationResult:
        reason = (
            decision.reason_code.value
            if isinstance(decision.reason_code, ReasonCode)
            else decision.reason_code
        )
        purchase = PurchaseRecord(
            purchase_id=purchase_id,
            mandate_id=mandate_id,
            intent_jti=_intent_jti(intent),
            status="rejected",
            reason_code=reason,
        )
        try:
            with self._transaction() as transaction:
                self._observe_intent(mandate_id, intent, now, transaction)
                updated = self._update_purchase(
                    purchase_id,
                    transaction=transaction,
                    status="rejected",
                    reason_code=reason,
                    reservation_id=None,
                    escalation_id=None,
                )
                if updated is None:
                    self._save_purchase(purchase, transaction)
                self._emit(
                    "purchase.rejected",
                    purchase_id,
                    {
                        "purchase_id": purchase_id,
                        "intent_jti": _intent_jti(intent),
                        "reason_code": reason,
                    },
                    now,
                    transaction,
                )
                if decision.diff and decision.diff.get("auto_suspend"):
                    self._emit(
                        "fraud.alert",
                        mandate_id,
                        {"mandate_id": mandate_id, "kind": "escalation_flood"},
                        now,
                        transaction,
                    )
        except Exception:
            self._update_purchase(
                purchase_id,
                status="rejected",
                reason_code=ReasonCode.MANDATE_SUSPENDED.value,
                reservation_id=None,
                escalation_id=None,
            )
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                purchase_id,
                "rejected",
            )
        return VerificationResult(decision, purchase_id, "rejected")

    def _escalated(
        self,
        *,
        mandate: Any,
        mandate_id: str,
        intent: Any,
        offer: Any,
        purchase_id: str,
        decision: Decision,
        now: datetime,
    ) -> VerificationResult:
        escalation = create_escalation(
            purchase_id=purchase_id,
            mandate_id=mandate_id,
            intent=intent,
            offer=offer,
            decision=decision,
            now=now,
        )
        reason = (
            decision.reason_code.value
            if isinstance(decision.reason_code, ReasonCode)
            else decision.reason_code
        )
        purchase = PurchaseRecord(
            purchase_id=purchase_id,
            mandate_id=mandate_id,
            intent_jti=_intent_jti(intent),
            status="awaiting_escalation",
            reason_code=reason,
            escalation_id=escalation.escalation_id,
        )
        try:
            with self._transaction() as transaction:
                self._observe_intent(mandate_id, intent, now, transaction)
                self._observe_escalation(mandate_id, now, decision, transaction)
                self._save_purchase(purchase, transaction)
                self._save_escalation(escalation, transaction)
                self._emit(
                    "purchase.escalated",
                    purchase_id,
                    {
                        "purchase_id": purchase_id,
                        "escalation_id": escalation.escalation_id,
                        "diff": dict(escalation.diff),
                    },
                    now,
                    transaction,
                )
                if decision.requires_uv or decision.reason_code in {
                    ReasonCode.STEPUP_AMOUNT_THRESHOLD,
                    ReasonCode.STEPUP_BUDGET_USAGE,
                }:
                    self._emit(
                        "risk.stepup_required",
                        purchase_id,
                        {
                            "purchase_id": purchase_id,
                            "level": escalation.level,
                            "reason_code": reason,
                        },
                        now,
                        transaction,
                    )
                if (decision.diff or {}).get("cooldown_until"):
                    self._emit(
                        "agent.paused_cooldown",
                        mandate_id,
                        {
                            "mandate_id": mandate_id,
                            "until": (decision.diff or {}).get("cooldown_until"),
                        },
                        now,
                        transaction,
                    )
        except Exception:
            self._update_purchase(
                purchase_id,
                status="rejected",
                reason_code=ReasonCode.MANDATE_SUSPENDED.value,
                reservation_id=None,
                escalation_id=None,
            )
            self._update_escalation(
                escalation.escalation_id,
                status="expired",
                decision=ReasonCode.MANDATE_SUSPENDED.value,
            )
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                purchase_id,
                "rejected",
            )
        return VerificationResult(
            decision, purchase_id, "awaiting_escalation", escalation.escalation_id
        )

    def _approved(
        self,
        *,
        mandate: Any,
        mandate_id: str,
        intent: Any,
        purchase_id: str,
        now: datetime,
    ) -> VerificationResult:
        reservation_id: str | None = None
        try:
            with self._transaction() as transaction:
                self._observe_intent(mandate_id, intent, now, transaction)
                reservation_id = self._reserve(
                    mandate,
                    mandate_id,
                    intent,
                    purchase_id,
                    transaction,
                )
                self._save_purchase(
                    PurchaseRecord(
                        purchase_id=purchase_id,
                        mandate_id=mandate_id,
                        intent_jti=_intent_jti(intent),
                        status="pending_capture",
                        reservation_id=reservation_id,
                    ),
                    transaction,
                )
                self._observe_open_authorization(mandate_id, now, transaction)
                decision = Decision(
                    DecisionValue.APPROVED,
                    reservation_id=reservation_id,
                )
                self._emit(
                    "purchase.verified",
                    purchase_id,
                    {"purchase_id": purchase_id, "reservation_id": reservation_id},
                    now,
                    transaction,
                )
        except ReservationRejected:
            return self._rejected(
                intent=intent,
                mandate_id=mandate_id,
                purchase_id=purchase_id,
                decision=Decision(DecisionValue.REJECTED, ReasonCode.BUDGET_EXCEEDED),
                now=now,
            )
        except Exception:
            if reservation_id is not None:
                released = self._release(
                    mandate_id,
                    _value(intent, "amount"),
                    reservation_id,
                    now,
                )
                if released:
                    self._release_open_authorization(mandate_id, now)
            return self._rejected(
                intent=intent,
                mandate_id=mandate_id,
                purchase_id=purchase_id,
                decision=Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                now=now,
            )
        return VerificationResult(decision, purchase_id, "pending_capture")

    def verify(
        self,
        intent: PurchaseIntent | Mapping[str, Any],
        *,
        now: datetime | None = None,
        offer: Any | None = None,
        mandate: Any | None = None,
        purchase_id: str | None = None,
        idempotency_request: Any = None,
        idempotency_scope: str | None = None,
    ) -> VerificationResult:
        """Run the gate and perform exactly one side-effect branch."""

        try:
            current = self._now(now)
        except ValueError:
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                purchase_id or f"purchase-{_intent_jti(intent)}",
                "rejected",
            )
        resolved_purchase_id = self._purchase_id(intent, purchase_id)
        mandate_jti = str(_value(intent, "mandate_jti", ""))
        try:
            idempotency_record, owns_idempotency_claim = self._claim_idempotency(
                intent,
                current,
                request=idempotency_request,
                scope=idempotency_scope,
            )
        except IdempotencyConflict:
            raise
        except Exception:
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                resolved_purchase_id,
                "rejected",
            )
        if idempotency_record is not None and idempotency_record.response is not None:
            return self._result_from_idempotency(
                idempotency_record.response,
                resolved_purchase_id,
            )
        if idempotency_record is not None and not owns_idempotency_claim:
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.RAIL_ERROR),
                resolved_purchase_id,
                "rejected",
            )

        def finish(result: VerificationResult) -> VerificationResult:
            try:
                return self._save_idempotency(idempotency_record, result, current)
            except Exception:
                return VerificationResult(
                    Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                    result.purchase_id,
                    "rejected",
                    result.escalation_id,
                )

        existing_purchase = self._read_purchase_by_intent(_intent_jti(intent))
        if existing_purchase is not None:
            return finish(self._result_for_existing_purchase(existing_purchase))
        loaded_mandate = mandate or self._read_mandate(mandate_jti)
        mandate_id = _mandate_id(loaded_mandate, mandate_jti)
        if loaded_mandate is None:
            return finish(
                self._rejected(
                    intent=intent,
                    mandate_id=mandate_id,
                    purchase_id=resolved_purchase_id,
                    decision=Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                    now=current,
                )
            )
        if self.offer_catalog is not None:
            resolved_offer = self._read_offer(str(_value(intent, "offer_id", "")))
        else:
            resolved_offer = offer
        if resolved_offer is None:
            return finish(
                self._rejected(
                    intent=intent,
                    mandate_id=mandate_id,
                    purchase_id=resolved_purchase_id,
                    decision=Decision(DecisionValue.REJECTED, ReasonCode.CONDITION_FAILED),
                    now=current,
                )
            )
        try:
            spend = self._spend(mandate_id, loaded_mandate, current)
            decision = self._evaluate(loaded_mandate, intent, spend, current, resolved_offer)
        except Exception:
            return finish(
                self._rejected(
                    intent=intent,
                    mandate_id=mandate_id,
                    purchase_id=resolved_purchase_id,
                    decision=Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                    now=current,
                )
            )
        if decision.is_rejected:
            return finish(
                self._rejected(
                    intent=intent,
                    mandate_id=mandate_id,
                    purchase_id=resolved_purchase_id,
                    decision=decision,
                    now=current,
                )
            )
        if decision.is_escalated:
            return finish(
                self._escalated(
                    mandate=loaded_mandate,
                    mandate_id=mandate_id,
                    intent=intent,
                    offer=resolved_offer,
                    purchase_id=resolved_purchase_id,
                    decision=decision,
                    now=current,
                )
            )
        return finish(
            self._approved(
                mandate=loaded_mandate,
                mandate_id=mandate_id,
                intent=intent,
                purchase_id=resolved_purchase_id,
                now=current,
            )
        )

    def _expire_and_compensate(
        self,
        record: EscalationRecord,
        *,
        now: datetime,
        reason: ReasonCode = ReasonCode.ESCALATION_TIMEOUT_DENIED,
        reservation_id: str | None = None,
    ) -> VerificationResult:
        expired = mark_expired(record)
        released = False
        release_id = reservation_id
        try:
            with self._transaction() as transaction:
                purchase = self._read_purchase(record.purchase_id)
                if purchase is not None and purchase.reservation_id:
                    release_id = purchase.reservation_id
                if release_id is not None:
                    released = self._release(
                        record.mandate_id,
                        _value(record.intent, "amount"),
                        release_id,
                        now,
                        transaction,
                    )
                    if released:
                        self._release_open_authorization(record.mandate_id, now, transaction)
                self._update_escalation(
                    record.escalation_id,
                    transaction=transaction,
                    status="expired",
                    decision=reason.value,
                )
                self._update_purchase(
                    record.purchase_id,
                    transaction=transaction,
                    status="compensated",
                    reason_code=reason.value,
                    reservation_id=None if released else release_id,
                )
                self._emit(
                    "escalation.expired",
                    record.escalation_id,
                    {"escalation_id": record.escalation_id, "purchase_id": record.purchase_id},
                    now,
                    transaction,
                )
        except Exception:
            if release_id is not None and not released:
                try:
                    released = self._release(
                        record.mandate_id,
                        _value(record.intent, "amount"),
                        release_id,
                        now,
                    )
                    if released:
                        self._release_open_authorization(record.mandate_id, now)
                except Exception:
                    pass
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.MANDATE_SUSPENDED),
                record.purchase_id,
                "compensated",
                record.escalation_id,
            )
        return VerificationResult(
            Decision(DecisionValue.REJECTED, reason),
            record.purchase_id,
            "compensated",
            expired.escalation_id,
        )

    def resolve_escalation(
        self,
        escalation_id: str,
        approval: str | bool,
        *,
        now: datetime | None = None,
        assertion: Any = None,
        uv_verified: bool | None = None,
    ) -> VerificationResult:
        """Resolve, re-gate, and only then reserve an escalated purchase."""

        current = self._now(now)
        record = self._read_escalation(escalation_id)
        if record is None:
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.ESCALATION_TIMEOUT_DENIED),
                f"purchase-{escalation_id}",
                "rejected",
                escalation_id,
            )
        if record.status != "pending":
            existing_purchase = self._read_purchase(record.purchase_id)
            if existing_purchase is not None:
                return self._result_for_existing_purchase(existing_purchase)
            return VerificationResult(
                Decision(DecisionValue.REJECTED, ReasonCode.ESCALATION_TIMEOUT_DENIED),
                record.purchase_id,
                "rejected",
                escalation_id,
            )
        if current >= record.timeout_at:
            return self._expire_and_compensate(record, now=current)
        envelope = resolve_envelope(
            record,
            current,
            approval,
            uv_verifier=self.uv_verifier,
            assertion=assertion,
            uv_verified=None,
        )
        if envelope.is_rejected:
            return self._expire_and_compensate(record, now=current)
        mandate = self._read_mandate(record.mandate_id) or self._read_mandate(
            str(_value(record.intent, "mandate_jti", record.mandate_id))
        )
        if mandate is None:
            return self._expire_and_compensate(
                record, now=current, reason=ReasonCode.MANDATE_SUSPENDED
            )
        if self.offer_catalog is not None:
            offer = self._read_offer(str(_value(record.intent, "offer_id", "")))
            if offer is None:
                return self._expire_and_compensate(
                    record,
                    now=current,
                    reason=ReasonCode.CONDITION_FAILED,
                )
        else:
            offer = record.offer
        try:
            spend = self._spend(record.mandate_id, mandate, current)
            decision = self._evaluate(
                mandate,
                record.intent,
                spend,
                current,
                offer,
                approved_stepup=True,
            )
        except Exception:
            return self._expire_and_compensate(
                record, now=current, reason=ReasonCode.MANDATE_SUSPENDED
            )
        if not decision.is_approved:
            reason = (
                decision.reason_code
                if isinstance(decision.reason_code, ReasonCode)
                else ReasonCode.ESCALATION_TIMEOUT_DENIED
            )
            return self._expire_and_compensate(record, now=current, reason=reason)
        reservation_id: str | None = None
        try:
            with self._transaction() as transaction:
                reservation_id = self._reserve(
                    mandate,
                    record.mandate_id,
                    record.intent,
                    record.purchase_id,
                    transaction,
                )
                self._update_escalation(
                    escalation_id,
                    transaction=transaction,
                    status="resolved",
                    decision="APPROVE",
                )
                self._update_purchase(
                    record.purchase_id,
                    transaction=transaction,
                    status="pending_capture",
                    reservation_id=reservation_id,
                    reason_code=None,
                )
                self._observe_open_authorization(record.mandate_id, current, transaction)
                self._emit(
                    "escalation.resolved",
                    escalation_id,
                    {"escalation_id": escalation_id, "decision": "APPROVE"},
                    current,
                    transaction,
                )
                self._emit(
                    "purchase.verified",
                    record.purchase_id,
                    {"purchase_id": record.purchase_id, "reservation_id": reservation_id},
                    current,
                    transaction,
                )
        except Exception:
            return self._expire_and_compensate(
                record,
                now=current,
                reason=ReasonCode.MANDATE_SUSPENDED,
                reservation_id=reservation_id,
            )
        return VerificationResult(
            Decision(DecisionValue.APPROVED, reservation_id=reservation_id),
            record.purchase_id,
            "pending_capture",
            escalation_id,
        )

    resolve = resolve_escalation


__all__ = ["DecisionService", "VerificationResult"]
