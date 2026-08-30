"""Audit and evidence module (Dev 2 ownership - decision 0019)."""

from .chain import validate_chain, validate_event
from .hashing import (
    GENESIS_PREV_HASH,
    canonical_json,
    compute_event_hash,
    compute_root_hash,
    normalize_utc,
)
from .models import AuditEvent, ChainResult, RootCheckpoint

__all__ = [
    "AuditEvent",
    "ChainResult",
    "RootCheckpoint",
    "GENESIS_PREV_HASH",
    "canonical_json",
    "compute_event_hash",
    "compute_root_hash",
    "normalize_utc",
    "validate_chain",
    "validate_event",
]
