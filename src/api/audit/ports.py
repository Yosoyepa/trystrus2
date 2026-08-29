"""Abstract ports for audit ledger persistence, cryptographic root signers,
witnesses, and clocks.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from .models import AuditEvent, RootCheckpoint


class Clock(Protocol):
    """Time source protocol."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Default system clock providing UTC datetimes."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class LedgerRepository(Protocol):
    """Persistence port for the append-only audit hash chain."""

    def append(
        self,
        *,
        mandate_id: str,
        type: str,
        payload: dict[str, Any],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        """Atomically compute hashes with tail-lock and append a new event."""
        ...

    def get_range(self, seq_start: int, seq_end: int) -> Sequence[AuditEvent]:
        """Fetch contiguous events within the specified sequence range [seq_start, seq_end]."""
        ...

    def get_by_mandate(self, mandate_id: str) -> Sequence[AuditEvent]:
        """Fetch all events associated with a mandate, ordered by seq."""
        ...

    def get_all(self) -> Sequence[AuditEvent]:
        """Fetch all audit events ordered by seq."""
        ...

    def get_tail(self) -> AuditEvent | None:
        """Fetch the most recent event in the ledger, if any."""
        ...

    def annotate_root(self, seq_start: int, seq_end: int, root_sig: str) -> int:
        """Guarded update: record root_sig on events in range WHERE root_sig IS NULL.

        Invariant #1: MUST NEVER mutate prev_hash, hash, payload, type, mandate_id, created_at.
        """
        ...


class RootSigner(Protocol):
    """Cryptographic signer port for evidence roots and signed webhooks (decision #15)."""

    def sign(self, data: bytes) -> bytes:
        """Sign binary payload with non-exportable evidence key."""
        ...

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify binary payload signature against public key."""
        ...

    @property
    def key_id(self) -> str:
        """Identifier or resource URI of the signing key."""
        ...


class Witness(Protocol):
    """External witness storage port for root checkpoints (decision #7, #11)."""

    def put(self, checkpoint: RootCheckpoint) -> None:
        """Publish an immutable root checkpoint to the external witness."""
        ...

    def get(self, seq_start: int, seq_end: int) -> RootCheckpoint | None:
        """Fetch a previously witnessed checkpoint for the given range, or None if absent."""
        ...


__all__ = [
    "Clock",
    "SystemClock",
    "LedgerRepository",
    "RootSigner",
    "Witness",
]
