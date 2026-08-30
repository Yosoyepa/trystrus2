"""Dependency-inversion ports for the evidence pack (R-EVIDENCE).

The pack aggregates across lanes, so it defines the minimal read surface it
needs instead of importing `decision/` or `audit/` directly: those modules
evolve on their own branches and the composition root wires the adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from .models import ChainVerdict


@runtime_checkable
class PurchaseReader(Protocol):
    def get_purchase(self, purchase_id: str) -> Mapping[str, Any] | None:
        """Return the purchase snapshot (status, reason, ids) or None."""


@runtime_checkable
class MandateClaimsReader(Protocol):
    def get_claims(self, mandate_jti: str) -> Mapping[str, Any] | None:
        """Return the verified mandate claims projection or None."""


@runtime_checkable
class IntentReader(Protocol):
    def get_intent(self, intent_jti: str) -> Mapping[str, Any] | None:
        """Return the original intent payload or None."""


@runtime_checkable
class ReceiptReader(Protocol):
    def get_receipt(self, purchase_id: str) -> Mapping[str, Any] | None:
        """Return the rail receipt snapshot or None if not captured yet."""


@runtime_checkable
class LedgerMirror(Protocol):
    def events_for(self, mandate_jti: str) -> tuple[Mapping[str, Any], ...]:
        """Return the ledger slice for the mandate, ordered by seq."""

    def chain_verdict(self, mandate_jti: str) -> ChainVerdict:
        """Fail-closed verification result over that slice."""


@runtime_checkable
class WitnessReader(Protocol):
    def latest_checkpoint(self, mandate_jti: str) -> Mapping[str, Any] | None:
        """Return the newest root checkpoint covering the mandate or None."""


__all__ = [
    "IntentReader",
    "LedgerMirror",
    "MandateClaimsReader",
    "PurchaseReader",
    "ReceiptReader",
    "WitnessReader",
]
