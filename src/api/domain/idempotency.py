"""Pure helpers for R-IDEM (derived idempotency and request fingerprints)."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

IDEMPOTENCY_TTL = timedelta(days=45)


def _secret_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, bytes):
        result = secret
    elif isinstance(secret, str):
        result = secret.encode("utf-8")
    else:
        raise TypeError("idempotency secret must be str or bytes")
    if not result:
        raise ValueError("idempotency secret cannot be empty")
    return result


def derive_idempotency_key(jti: str, secret: str | bytes) -> str:
    """Derive the stable rail key ``HMAC-SHA256(secret, jti).hexdigest()``."""

    if not isinstance(jti, str) or not jti:
        raise ValueError("intent jti must be a non-empty string")
    return hmac.new(_secret_bytes(secret), jti.encode("utf-8"), hashlib.sha256).hexdigest()


def idem_key(jti: str, secret: str | bytes) -> str:
    """Contract vocabulary alias for :func:`derive_idempotency_key`."""

    return derive_idempotency_key(jti, secret)


def idempotency_key(jti: str, secret: str | bytes) -> str:
    return derive_idempotency_key(jti, secret)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("canonical JSON requires aware datetimes")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a request deterministically for local replay/body checks.

    This is the domain's compact canonical representation for fingerprints;
    it sorts object keys and uses no insignificant whitespace.  It is not a
    replacement for the detached-JWS/JCS implementation owned elsewhere.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )


def request_fingerprint(value: Any) -> str:
    """Return a stable SHA-256 digest for an idempotent request body."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


canonical_request_fingerprint = request_fingerprint


class IdempotencyConflict(ValueError):
    """Raised when one derived key is reused for a different request body."""


def _ensure_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("idempotency timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """The domain portion of an ``idempotency_keys`` row."""

    key: str
    scope: str
    derived_from: str
    request_fingerprint: str
    expires_at: datetime
    response: Mapping[str, Any] | None = None
    created_at: datetime | None = None
    claim_token: str | None = None

    def __post_init__(self) -> None:
        if not self.key or not self.scope or not self.derived_from:
            raise ValueError("idempotency records require key, scope, and source jti")
        object.__setattr__(self, "expires_at", _ensure_utc(self.expires_at))
        if self.created_at is not None:
            created_at = _ensure_utc(self.created_at)
            if self.expires_at <= created_at:
                raise ValueError("idempotency expiry must be after creation")
            object.__setattr__(self, "created_at", created_at)

    def is_expired(self, now: datetime) -> bool:
        return _ensure_utc(now) >= self.expires_at


def make_record(
    jti: str,
    secret: str | bytes,
    scope: str,
    request: Any,
    created_at: datetime,
    *,
    ttl: timedelta = IDEMPOTENCY_TTL,
    response: Mapping[str, Any] | None = None,
    claim_token: str | None = None,
) -> IdempotencyRecord:
    """Build a record with the contract's default 45-day retention."""

    created = _ensure_utc(created_at)
    if ttl <= timedelta(0):
        raise ValueError("idempotency TTL must be positive")
    return IdempotencyRecord(
        key=derive_idempotency_key(jti, secret),
        scope=scope,
        derived_from=jti,
        request_fingerprint=request_fingerprint(request),
        expires_at=created + ttl,
        response=response,
        created_at=created,
        claim_token=claim_token,
    )


def validate_reuse(
    record: IdempotencyRecord,
    jti: str,
    secret: str | bytes,
    scope: str,
    request: Any,
    now: datetime,
) -> bool:
    """Validate that a retry is the same operation and is still retained.

    Expired records return ``False`` so the caller may create a fresh record.
    A scope or body mismatch raises ``IdempotencyConflict`` before any rail
    call, as required by R-IDEM.
    """

    if record.is_expired(now):
        return False
    expected_key = derive_idempotency_key(jti, secret)
    fingerprint = request_fingerprint(request)
    if record.key != expected_key or record.derived_from != jti or record.scope != scope:
        raise IdempotencyConflict("idempotency key is bound to another operation")
    if not hmac.compare_digest(record.request_fingerprint, fingerprint):
        raise IdempotencyConflict("same idempotency key used with a different body")
    return True


__all__ = [
    "IDEMPOTENCY_TTL",
    "derive_idempotency_key",
    "idem_key",
    "idempotency_key",
    "canonical_json",
    "request_fingerprint",
    "canonical_request_fingerprint",
    "IdempotencyConflict",
    "IdempotencyRecord",
    "make_record",
    "validate_reuse",
]
