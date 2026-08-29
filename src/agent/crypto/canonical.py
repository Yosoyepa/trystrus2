"""Canonical JSON (RFC 8785 / JCS subset).

Property C3/M7: without a canonical serialisation two services sign different
bytes for the same object and every signature fails.  Floats are rejected on
purpose -- money travels as a fixed 2-decimal string (M7), so a float in a
signed payload is a bug, not a value.
"""
from __future__ import annotations
import json
from decimal import Decimal
from typing import Any


class NonCanonical(ValueError):
    pass


def _check(value: Any, path: str = "$") -> Any:
    if isinstance(value, float):
        raise NonCanonical(f"float at {path}: use a 2-decimal string for money (M7)")
    if isinstance(value, Decimal):
        raise NonCanonical(f"Decimal at {path}: serialise with money.fmt() first (M7)")
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise NonCanonical(f"non-string key at {path}")
        return {k: _check(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_check(v, f"{path}[{i}]") for i, v in enumerate(value)]
    raise NonCanonical(f"unserialisable {type(value).__name__} at {path}")


def canonical_json(obj: Any) -> str:
    """Deterministic string: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(
        _check(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")
