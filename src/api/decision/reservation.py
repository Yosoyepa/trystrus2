"""Application-level reservation helpers.

The actual conditional SQL lives in ``repository_postgres.py``.  This module
keeps the verify use case independent from that driver and makes reservation
failure an explicit, fail-closed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.api.domain.models import ReasonCode

from .ports import AtomicReservationStore


class ReservationRejected(RuntimeError):
    """Raised when the atomic mandate update matches zero rows."""

    reason_code = ReasonCode.BUDGET_EXCEEDED


@dataclass(frozen=True, slots=True)
class ReservationResult:
    reservation_id: str
    mandate_id: str
    amount: Decimal


class ReservationCoordinator:
    """Translate a conditional reservation port into a small use-case API."""

    def __init__(self, store: AtomicReservationStore) -> None:
        self.store = store

    def reserve(
        self,
        mandate_id: str,
        amount: Decimal | str,
        total_budget: Decimal | str,
        *,
        max_txn_count: int | None = None,
        reservation_id: str | None = None,
        transaction: Any = None,
    ) -> ReservationResult:
        result = self.store.reserve(
            mandate_id,
            amount,
            total_budget,
            max_txn_count=max_txn_count,
            reservation_id=reservation_id,
            transaction=transaction,
        )
        if not result:
            raise ReservationRejected("the mandate reservation condition was not met")
        return ReservationResult(str(result), mandate_id, Decimal(str(amount)))

    def release(
        self,
        mandate_id: str,
        amount: Decimal | str,
        reservation_id: str | None = None,
        *,
        transaction: Any = None,
    ) -> bool:
        return self.store.release(
            mandate_id,
            amount,
            reservation_id,
            transaction=transaction,
        )


def reserve_atomically(
    store: AtomicReservationStore,
    mandate_id: str,
    amount: Decimal | str,
    total_budget: Decimal | str,
    *,
    max_txn_count: int | None = None,
    reservation_id: str | None = None,
    transaction: Any = None,
) -> ReservationResult:
    """Reserve through the port and fail closed when its guard rejects."""

    return ReservationCoordinator(store).reserve(
        mandate_id,
        amount,
        total_budget,
        max_txn_count=max_txn_count,
        reservation_id=reservation_id,
        transaction=transaction,
    )


__all__ = [
    "ReservationCoordinator",
    "ReservationRejected",
    "ReservationResult",
    "reserve_atomically",
]
