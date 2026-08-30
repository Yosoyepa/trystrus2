"""The single canonical JSON serialization for evidence-grade hashing.

RT-9 (gate/gap analysis): the codebase had three divergent canonical JSONs
(domain fingerprints, audit hashing, service event digests). Evidence packs
cross-check hashes across those layers, so every evidence-grade producer must
serialize with THIS module after the lanes merge.

Contract (strict, deterministic):
- Object keys sorted recursively; compact separators; UTF-8; ``allow_nan`` off.
- Floats are forbidden anywhere in the structure (money is ``Decimal``/str).
- ``Decimal`` becomes fixed-point string; ``datetime`` must be timezone-aware
  and becomes ``...Z`` UTC; ``Enum`` becomes its value; dataclasses become
  dicts; mapping keys must already be strings (so ``1`` and ``"1"`` cannot
  collide).
- ``set``/``frozenset`` are rejected: unordered input cannot have a canonical
  form, callers must pass ordered lists (audit/hashing coerced them to
  insertion-ordered lists, which is not deterministic across producers).

This module is a leaf: it imports the standard library only, so domain,
decision, audit, and events can all depend on it without cycles.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import enum
import hashlib
import json
from typing import Any

__all__ = [
    "canonical_bytes",
    "canonical_json",
    "normalize_utc",
    "sha256_hex",
]


def normalize_utc(value: _dt.datetime | str) -> str:
    """Normalize an aware datetime (or ISO string) to a UTC ``...Z`` string.

    Naive datetimes are rejected: canonical evidence must not depend on the
    producer's local timezone.
    """

    if isinstance(value, str):
        try:
            parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO datetime string: {value}") from exc
        value = parsed
    if not isinstance(value, _dt.datetime):
        raise TypeError(f"expected datetime or str, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("canonical timestamps must be timezone-aware")
    return value.astimezone(_dt.UTC).isoformat().replace("+00:00", "Z")


def _reject(value: Any, reason: str) -> None:
    raise TypeError(reason)


def _default(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        _reject(value, "floating point numbers are forbidden in canonical serialization")
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, _dt.datetime):
        return normalize_utc(value)
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (set, frozenset)):
        _reject(
            value,
            "sets are unordered and have no canonical form; pass a sorted list",
        )
    if isinstance(value, bytes):
        _reject(value, "bytes have no canonical JSON form; encode deliberately")
    raise TypeError(f"type {type(value).__name__} is not canonical-JSON serializable")


def _check(value: Any) -> None:
    if isinstance(value, float):
        _reject(value, "floating point numbers are forbidden in canonical serialization")
    if isinstance(value, (set, frozenset)):
        _reject(value, "sets are unordered and have no canonical form; pass a sorted list")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                _reject(
                    k,
                    'mapping keys must be strings so that 1 and "1" cannot collide',
                )
            _check(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check(item)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        _check(dataclasses.asdict(value))


def canonical_json(value: Any) -> str:
    """Serialize ``value`` deterministically for evidence-grade hashing."""

    _check(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_default,
    )


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 bytes of :func:`canonical_json` (the hashing input)."""

    return canonical_json(value).encode("utf-8")


def sha256_hex(value: Any) -> str:
    """Convenience: SHA-256 hex digest over :func:`canonical_bytes`."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()
