"""Pure cryptographic chain validation for audit events (no I/O)."""

from __future__ import annotations

from collections.abc import Sequence

from .hashing import GENESIS_PREV_HASH, compute_event_hash
from .models import AuditEvent, ChainResult


def validate_event(event: AuditEvent, expected_prev_hash: str) -> tuple[bool, str | None]:
    """Validate a single event's previous hash link and cryptographic hash.

    Returns (True, None) on success or (False, reason) on failure.
    """
    if event.prev_hash != expected_prev_hash:
        return (
            False,
            f"prev_hash mismatch: expected {expected_prev_hash}, got {event.prev_hash}",
        )

    expected_hash = compute_event_hash(
        mandate_id=event.mandate_id,
        type=event.type,
        payload=event.payload,
        prev_hash=event.prev_hash,
        created_at=event.created_at,
    )
    if event.hash != expected_hash:
        return (
            False,
            f"hash mismatch: recomputed {expected_hash}, recorded {event.hash}",
        )

    return (True, None)


def validate_chain(
    events: Sequence[AuditEvent],
    expected_start_prev_hash: str | None = None,
    check_seq_continuity: bool = True,
) -> ChainResult:
    """Validate an ordered list of audit events for unbroken hash chaining and determinism.

    Fail-closed: any gap, hash corruption, prev_hash divergence, or payload mutation
    immediately returns `ChainResult(ok=False, first_bad_seq=..., reason=...)`.
    """
    if not events:
        return ChainResult(ok=True, verified_count=0, last_hash=None)

    for i, event in enumerate(events):
        seq = event.seq if event.seq is not None else (i + 1)

        # 1. Sequence continuity check
        if check_seq_continuity and i > 0:
            prev_event = events[i - 1]
            if prev_event.seq is not None and event.seq is not None:
                if event.seq != prev_event.seq + 1:
                    return ChainResult(
                        ok=False,
                        first_bad_seq=event.seq,
                        reason=(
                            f"sequence gap detected at index {i}: "
                            f"expected seq {prev_event.seq + 1}, got {event.seq}"
                        ),
                        verified_count=i,
                    )

        # 2. Determine expected prev_hash
        if i == 0:
            if expected_start_prev_hash is not None:
                expected_prev = expected_start_prev_hash
            elif event.seq == 1:
                expected_prev = GENESIS_PREV_HASH
            else:
                expected_prev = event.prev_hash
        else:
            expected_prev = events[i - 1].hash

        # 3. Check event validity
        valid, reason = validate_event(event, expected_prev)
        if not valid:
            return ChainResult(
                ok=False,
                first_bad_seq=seq,
                reason=reason,
                verified_count=i,
            )

    return ChainResult(
        ok=True,
        verified_count=len(events),
        last_hash=events[-1].hash,
    )


__all__ = [
    "validate_chain",
    "validate_event",
]
