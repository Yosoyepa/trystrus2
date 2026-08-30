"""Unit tests for pure audit chain algebra (hashing, canonicalization, and validation)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from src.api.audit.chain import validate_chain
from src.api.audit.hashing import (
    GENESIS_PREV_HASH,
    canonical_json,
    compute_event_hash,
    compute_root_hash,
)
from src.api.audit.models import AuditEvent, RootCheckpoint


def _make_event(
    seq: int,
    prev_hash: str,
    payload: dict,
    mandate_id: str = "mdt_test_01",
    event_type: str = "mandate.created",
    created_at: datetime | None = None,
) -> AuditEvent:
    ts = created_at or datetime(2026, 8, 29, 12, 0, seq, tzinfo=UTC)
    h = compute_event_hash(
        mandate_id=mandate_id,
        type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        created_at=ts,
    )
    return AuditEvent(
        seq=seq,
        mandate_id=mandate_id,
        type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        hash=h,
        created_at=ts,
    )


class TestCanonicalJson:
    def test_key_ordering_is_deterministic(self) -> None:
        dict_a = {"b": 2, "a": 1, "z": {"y": 10, "x": 5}}
        dict_b = {"z": {"x": 5, "y": 10}, "a": 1, "b": 2}
        assert canonical_json(dict_a) == canonical_json(dict_b)
        assert canonical_json(dict_a) == '{"a":1,"b":2,"z":{"x":5,"y":10}}'

    def test_decimal_and_datetime_formatting(self) -> None:
        data = {
            "amount": Decimal("130.00"),
            "created_at": datetime(2026, 8, 29, 14, 30, 0, tzinfo=UTC),
        }
        rendered = canonical_json(data)
        assert rendered == '{"amount":"130.00","created_at":"2026-08-29T14:30:00Z"}'

    def test_floating_point_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="floating point numbers are forbidden"):
            canonical_json({"price": 150.50})

    def test_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            canonical_json({"ts": datetime(2026, 8, 29, 12, 0, 0)})


class TestEventHashing:
    def test_event_hash_stability(self) -> None:
        ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
        h1 = compute_event_hash(
            mandate_id="mdt_01",
            type="purchase.verified",
            payload={"amount": Decimal("100.00"), "merchant": "vuelaya"},
            prev_hash=GENESIS_PREV_HASH,
            created_at=ts,
        )
        h2 = compute_event_hash(
            mandate_id="mdt_01",
            type="purchase.verified",
            payload={"merchant": "vuelaya", "amount": Decimal("100.00")},
            prev_hash=GENESIS_PREV_HASH,
            created_at=ts,
        )
        assert h1 == h2
        assert len(h1) == 64

    def test_hash_sensitive_to_every_field(self) -> None:
        ts = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
        base = {
            "mandate_id": "mdt_01",
            "type": "purchase.verified",
            "payload": {"amount": "100.00"},
            "prev_hash": GENESIS_PREV_HASH,
            "created_at": ts,
        }
        base_hash = compute_event_hash(**base)

        # Vary mandate_id
        assert compute_event_hash(**{**base, "mandate_id": "mdt_02"}) != base_hash
        # Vary type
        assert compute_event_hash(**{**base, "type": "purchase.captured"}) != base_hash
        # Vary payload
        assert compute_event_hash(**{**base, "payload": {"amount": "100.01"}}) != base_hash
        # Vary prev_hash
        assert compute_event_hash(**{**base, "prev_hash": "1" * 64}) != base_hash
        # Vary created_at
        ts2 = datetime(2026, 8, 29, 12, 0, 1, tzinfo=UTC)
        assert compute_event_hash(**{**base, "created_at": ts2}) != base_hash


class TestChainValidation:
    def test_valid_chain_passes(self) -> None:
        e1 = _make_event(1, GENESIS_PREV_HASH, {"step": 1})
        e2 = _make_event(2, e1.hash, {"step": 2})
        e3 = _make_event(3, e2.hash, {"step": 3})

        result = validate_chain([e1, e2, e3])
        assert result.ok is True
        assert result.verified_count == 3
        assert result.last_hash == e3.hash
        assert result.first_bad_seq is None

    def test_empty_chain_is_valid(self) -> None:
        result = validate_chain([])
        assert result.ok is True
        assert result.verified_count == 0
        assert result.last_hash is None

    def test_mutated_payload_fails_with_exact_seq(self) -> None:
        e1 = _make_event(1, GENESIS_PREV_HASH, {"step": 1})
        e2 = _make_event(2, e1.hash, {"step": 2})
        e3 = _make_event(3, e2.hash, {"step": 3})

        # Mutate payload of e2 without recomputing its hash
        tampered_e2 = AuditEvent(
            seq=e2.seq,
            mandate_id=e2.mandate_id,
            type=e2.type,
            payload={"step": 2, "tampered": True},
            prev_hash=e2.prev_hash,
            hash=e2.hash,
            created_at=e2.created_at,
        )

        result = validate_chain([e1, tampered_e2, e3])
        assert result.ok is False
        assert result.first_bad_seq == 2
        assert "hash mismatch" in (result.reason or "")
        assert result.verified_count == 1

    def test_corrupted_prev_hash_fails(self) -> None:
        e1 = _make_event(1, GENESIS_PREV_HASH, {"step": 1})
        bad_prev = "f" * 64
        e2 = _make_event(2, bad_prev, {"step": 2})

        result = validate_chain([e1, e2])
        assert result.ok is False
        assert result.first_bad_seq == 2
        assert "prev_hash mismatch" in (result.reason or "")

    def test_sequence_gap_fails(self) -> None:
        e1 = _make_event(1, GENESIS_PREV_HASH, {"step": 1})
        e3 = _make_event(3, e1.hash, {"step": 3})  # skipped seq 2

        result = validate_chain([e1, e3])
        assert result.ok is False
        assert result.first_bad_seq == 3
        assert "sequence gap" in (result.reason or "")


class TestRootCheckpointModel:
    def test_compute_root_hash_and_checkpoint(self) -> None:
        h = compute_root_hash(
            seq_start=1,
            seq_end=10,
            last_hash="a" * 64,
            cardinality=10,
        )
        assert len(h) == 64
        cp = RootCheckpoint(
            seq_start=1,
            seq_end=10,
            root_hash=h,
            root_sig="sig_test_mock",
            cardinality=10,
        )
        data = cp.to_dict()
        assert data["seq_start"] == 1
        assert data["seq_end"] == 10
        assert data["cardinality"] == 10
        assert data["root_hash"] == h
