"""Shared singletons for the kernel's routers.

Plain module-level accessors rather than FastAPI `Depends` for the stateless
services: they hold no per-request state, and tests override them by assigning
to the module. The database session stays a real dependency — that one *is*
per-request, because it is a transaction.
"""

from __future__ import annotations

from typing import Any

from .config import settings
from .services.mandate_registry import MandateRegistry
from .services.passkey import PasskeyService
from .services.rail_client import YunoSimRail

_registry: MandateRegistry | None = None
_passkey: PasskeyService | None = None
_rail: YunoSimRail | None = None
_decision_service: Any | None = None
_ledger_service: Any | None = None
_evidence_service: Any | None = None
_agent_conn: Any | None = None


def mandate_registry() -> MandateRegistry:
    global _registry
    if _registry is None:
        _registry = MandateRegistry()
    return _registry


def passkey_service() -> PasskeyService:
    global _passkey
    if _passkey is None:
        _passkey = PasskeyService()
    return _passkey


def rail() -> YunoSimRail:
    global _rail
    if _rail is None:
        _rail = YunoSimRail(base_url=settings().yuno_sim_url)
    return _rail


def decision_service() -> Any:
    global _decision_service
    if _decision_service is None:
        from src.api.decision.repository_memory import (
            InMemoryEscalationStore,
            InMemoryIdempotencyStore,
            InMemoryMandateReader,
            InMemoryOfferCatalog,
            InMemoryOutboxWriter,
            InMemoryPurchaseStore,
            InMemoryReservationStore,
            InMemoryVelocityStore,
        )
        from src.api.decision.service import DecisionService

        _decision_service = DecisionService(
            mandate_reader=InMemoryMandateReader(),
            offer_catalog=InMemoryOfferCatalog(),
            velocity_store=InMemoryVelocityStore(),
            reservation_store=InMemoryReservationStore(),
            purchase_store=InMemoryPurchaseStore(),
            escalation_store=InMemoryEscalationStore(),
            outbox=InMemoryOutboxWriter(),
            idempotency_store=InMemoryIdempotencyStore("api-secret"),
        )
    return _decision_service


def set_decision_service(service: Any) -> None:
    global _decision_service
    _decision_service = service


def ledger_service() -> Any:
    global _ledger_service
    if _ledger_service is None:
        from src.api.audit.repository_memory import InMemoryLedgerRepository
        from src.api.audit.service import LedgerService
        from src.api.audit.signer_local import LocalEd25519Signer
        from src.api.audit.witness_memory import InMemoryWitness

        _ledger_service = LedgerService(
            repository=InMemoryLedgerRepository(),
            signer=LocalEd25519Signer(),
            witness=InMemoryWitness(),
        )
    return _ledger_service


def set_ledger_service(service: Any) -> None:
    global _ledger_service
    _ledger_service = service


class _DefaultEvidenceReaders:
    """Default memory reader adapters for EvidenceService."""

    def __init__(self) -> None:
        self.purchases: dict[str, dict[str, Any]] = {}
        self.mandates: dict[str, dict[str, Any]] = {}
        self.intents: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.checkpoints: dict[str, dict[str, Any]] = {}

    def get_purchase(self, purchase_id: str) -> dict[str, Any] | None:
        return self.purchases.get(purchase_id)

    def get_claims(self, mandate_jti: str) -> dict[str, Any] | None:
        return self.mandates.get(mandate_jti)

    def get_intent(self, intent_jti: str) -> dict[str, Any] | None:
        return self.intents.get(intent_jti)

    def get_receipt(self, purchase_id: str) -> dict[str, Any] | None:
        return self.receipts.get(purchase_id)

    def events_for(self, mandate_jti: str) -> tuple[dict[str, Any], ...]:
        return tuple(self.events.get(mandate_jti, []))

    def chain_verdict(self, mandate_jti: str) -> Any:
        from src.api.evidence.models import ChainVerdict

        return ChainVerdict(ok=True)

    def latest_checkpoint(self, mandate_jti: str) -> dict[str, Any] | None:
        return self.checkpoints.get(mandate_jti)


def evidence_service() -> Any:
    global _evidence_service
    if _evidence_service is None:
        from src.api.evidence.service import EvidenceService

        readers = _DefaultEvidenceReaders()
        _evidence_service = EvidenceService(
            purchases=readers,
            mandates=readers,
            intents=readers,
            receipts=readers,
            ledger=readers,
            witness=readers,
        )
    return _evidence_service


def set_evidence_service(service: Any) -> None:
    global _evidence_service
    _evidence_service = service


def agent_conn() -> Any:
    global _agent_conn
    if _agent_conn is not None:
        return _agent_conn
    try:
        from src.agent import db

        return db.connect()
    except Exception:
        return None


def set_agent_conn(conn: Any) -> None:
    global _agent_conn
    _agent_conn = conn


def reset() -> None:
    """Drop the singletons. For tests that change configuration."""
    global _registry, _passkey, _rail, _decision_service
    global _ledger_service, _evidence_service, _agent_conn
    _registry = _passkey = _rail = _decision_service = _ledger_service = _evidence_service = (
        _agent_conn
    ) = None
