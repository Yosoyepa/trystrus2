"""Application-level idempotency claim coordination for the DEV2 core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.api.domain.idempotency import IdempotencyRecord, make_record


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """The stored record and whether this caller owns the pending claim."""

    record: IdempotencyRecord
    owns_claim: bool


def claim_idempotency(
    store: Any,
    *,
    jti: str,
    secret: str | bytes,
    scope: str,
    request: Any,
    now: datetime,
) -> IdempotencyClaim:
    """Reserve a derived key and identify concurrent callers safely.

    A fresh claim token is embedded in the record metadata by the repository.
    A caller that observes another token must not repeat business side effects;
    it can retry after the owner stores the first response.
    """

    candidate = make_record(
        jti,
        secret,
        scope,
        request,
        now,
        claim_token=uuid4().hex,
    )
    reserve = getattr(store, "reserve", None)
    if reserve is None:
        raise RuntimeError("idempotency store does not expose reserve")
    reserved = reserve(candidate)
    return IdempotencyClaim(
        record=reserved,
        owns_claim=reserved.claim_token == candidate.claim_token,
    )


__all__ = ["IdempotencyClaim", "claim_idempotency"]
