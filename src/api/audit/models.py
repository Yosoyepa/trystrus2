"""Data models for audit ledger, chain verification, and root checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def ensure_aware_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC and ensure it is timezone-aware."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable audit event node in the hash chain."""

    mandate_id: str
    type: str
    payload: Mapping[str, Any]
    prev_hash: str
    hash: str
    created_at: datetime
    seq: int | None = None
    root_sig: str | None = None

    def __post_init__(self) -> None:
        if not self.mandate_id:
            raise ValueError("mandate_id cannot be empty")
        if not self.type:
            raise ValueError("event type cannot be empty")
        if not self.prev_hash or len(self.prev_hash) != 64:
            raise ValueError("prev_hash must be a 64-character hex string")
        if not self.hash or len(self.hash) != 64:
            raise ValueError("hash must be a 64-character hex string")
        created_at = ensure_aware_utc(self.created_at)
        object.__setattr__(self, "created_at", created_at)
        if self.seq is not None and self.seq < 1:
            raise ValueError("seq must be a positive integer if present")

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary representation matching database columns."""
        return {
            "seq": self.seq,
            "mandate_id": self.mandate_id,
            "type": self.type,
            "payload": dict(self.payload),
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "root_sig": self.root_sig,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ChainResult:
    """Outcome of verifying a contiguous chain of audit events."""

    ok: bool
    first_bad_seq: int | None = None
    reason: str | None = None
    verified_count: int = 0
    last_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.ok and self.reason is None:
            raise ValueError("failed chain verification must provide a reason")
        if self.ok and self.first_bad_seq is not None:
            raise ValueError("successful chain verification cannot have first_bad_seq")


@dataclass(frozen=True, slots=True)
class RootCheckpoint:
    """Evidence root checkpoint signed via KMS and published to witness storage."""

    seq_start: int
    seq_end: int
    root_hash: str
    root_sig: str
    cardinality: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.seq_start < 1 or self.seq_end < self.seq_start:
            raise ValueError("invalid seq range for root checkpoint")
        if self.cardinality != (self.seq_end - self.seq_start + 1):
            raise ValueError("cardinality must match seq_end - seq_start + 1")
        if not self.root_hash or len(self.root_hash) != 64:
            raise ValueError("root_hash must be a 64-character hex string")
        if not self.root_sig:
            raise ValueError("root_sig cannot be empty")
        created_at = ensure_aware_utc(self.created_at)
        object.__setattr__(self, "created_at", created_at)

    def to_dict(self) -> dict[str, Any]:
        """Canonical dictionary representation for witness storage."""
        return {
            "seq_start": self.seq_start,
            "seq_end": self.seq_end,
            "root_hash": self.root_hash,
            "root_sig": self.root_sig,
            "cardinality": self.cardinality,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }


__all__ = [
    "AuditEvent",
    "ChainResult",
    "RootCheckpoint",
    "ensure_aware_utc",
]
