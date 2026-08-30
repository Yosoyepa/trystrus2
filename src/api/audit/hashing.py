"""Pure canonical JSON serialization and cryptographic hash computations for audit ledger."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

GENESIS_PREV_HASH: str = "0" * 64


def normalize_utc(value: datetime | str) -> str:
    """Normalize a datetime to standard UTC ISO-8601 string with 'Z' suffix."""
    if isinstance(value, str):
        # Validate that string is parseable and aware or ISO formatted
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO datetime string: {value}") from exc
    elif isinstance(value, datetime):
        dt = value
    else:
        raise TypeError(f"expected datetime or str, got {type(value).__name__}")

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise TypeError("floating point numbers are forbidden in canonical audit serialization")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return normalize_utc(value)
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, (Sequence, set, frozenset)) and not isinstance(value, (str, bytes)):
        return list(value)
    raise TypeError(f"type {type(value).__name__} is not JSON serializable in canonical audit")


def _check_no_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("floating point numbers are forbidden in canonical audit serialization")
    if isinstance(value, Mapping):
        for k, v in value.items():
            _check_no_floats(k)
            _check_no_floats(v)
    elif isinstance(value, (Sequence, set, frozenset)) and not isinstance(value, (str, bytes)):
        for item in value:
            _check_no_floats(item)
    elif dataclasses.is_dataclass(value):
        _check_no_floats(dataclasses.asdict(value))


def canonical_json(value: Any) -> str:
    """Serialize data deterministically using sorted keys, compact spacing, and no floats."""
    _check_no_floats(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )


def compute_event_hash(
    *,
    mandate_id: str,
    type: str,
    payload: Mapping[str, Any] | Any,
    prev_hash: str,
    created_at: datetime | str,
) -> str:
    """Compute the deterministic SHA-256 hash of an audit event.

    Canonical representation:
    hash = sha256(canonical_json({
        "created_at": iso_str,
        "mandate_id": mandate_id,
        "payload": payload,
        "prev_hash": prev_hash,
        "type": type,
    }))

    Note: `seq` is excluded from the hash by invariant #3 (seq is database order, not content).
    """
    if not mandate_id:
        raise ValueError("mandate_id cannot be empty")
    if not type:
        raise ValueError("event type cannot be empty")
    if not prev_hash or len(prev_hash) != 64:
        raise ValueError("prev_hash must be a 64-character hex string")

    iso_created_at = normalize_utc(created_at)
    envelope = {
        "created_at": iso_created_at,
        "mandate_id": mandate_id,
        "payload": payload,
        "prev_hash": prev_hash,
        "type": type,
    }
    canonical_bytes = canonical_json(envelope).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def compute_root_hash(
    *,
    seq_start: int,
    seq_end: int,
    last_hash: str,
    cardinality: int,
) -> str:
    """Compute the deterministic SHA-256 root hash over an event sequence range."""
    if seq_start < 1 or seq_end < seq_start:
        raise ValueError("invalid sequence range for root computation")
    if cardinality != (seq_end - seq_start + 1):
        raise ValueError("cardinality must equal seq_end - seq_start + 1")
    if not last_hash or len(last_hash) != 64:
        raise ValueError("last_hash must be a 64-character hex string")

    envelope = {
        "cardinality": cardinality,
        "last_hash": last_hash,
        "seq_end": seq_end,
        "seq_start": seq_start,
    }
    canonical_bytes = canonical_json(envelope).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


__all__ = [
    "GENESIS_PREV_HASH",
    "canonical_json",
    "compute_event_hash",
    "compute_root_hash",
    "normalize_utc",
]
