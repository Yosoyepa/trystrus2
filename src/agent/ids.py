"""Identifiers and time. One clock, one id shape, so the log sorts sanely."""
from __future__ import annotations
import datetime as _dt
import secrets
import uuid


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def now_ts() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def nonce() -> str:
    """>= 128 bits of randomness (C7)."""
    return secrets.token_hex(16)
