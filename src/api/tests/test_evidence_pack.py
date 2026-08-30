"""Tests for the R-EVIDENCE pack assembly (fail-closed)."""

from __future__ import annotations

import datetime
import json

import pytest
from src.api.evidence import (
    INTEGRITY_FAILED,
    INTEGRITY_OK,
    ChainVerdict,
    EvidenceNotFoundError,
    EvidenceService,
    pack_canonical_bytes,
)

UTC = datetime.UTC
NOW = datetime.datetime(2026, 8, 29, 13, 0, tzinfo=UTC)

PURCHASE = {
    "purchase_id": "purchase-jti-1",
    "intent_jti": "jti-1",
    "status": "captured",
    "reason_code": None,
    "reservation_id": "reservation:purchase-jti-1",
    "captured_at": "2026-08-29T12:05:00Z",
}

MANDATE = {"jti": "jti-1", "status": "active", "limits": {"max_per_txn": "150.00"}}
INTENT = {"jti": "jti-1", "amount": "120.00", "merchant_id": "m-1", "currency": "USD"}
RECEIPT = {"capture_id": "cap-1", "amount": "120.00", "status": "COMPLETED"}
EVENTS = (
    {"seq": 1, "type": "purchase.requested", "hash": "a" * 64},
    {"seq": 2, "type": "purchase.verified", "hash": "b" * 64},
    {"seq": 3, "type": "purchase.captured", "hash": "c" * 64},
)
CHECKPOINT = {"seq_start": 1, "seq_end": 3, "root_sig": "d" * 128}


class FakeReaders:
    def __init__(
        self,
        *,
        purchase=PURCHASE,
        mandate=MANDATE,
        intent=INTENT,
        receipt=RECEIPT,
        events=EVENTS,
        chain_ok=True,
        chain_reason=None,
        checkpoint=CHECKPOINT,
    ) -> None:
        self._purchase = purchase
        self._mandate = mandate
        self._intent = intent
        self._receipt = receipt
        self._events = events
        self._chain = ChainVerdict(ok=chain_ok, reason=chain_reason)
        self._checkpoint = checkpoint

    def get_purchase(self, purchase_id):
        return dict(self._purchase) if purchase_id == PURCHASE["purchase_id"] else None

    def get_claims(self, mandate_jti):
        return dict(self._mandate) if mandate_jti == "jti-1" else None

    def get_intent(self, intent_jti):
        return dict(self._intent) if intent_jti == "jti-1" else None

    def get_receipt(self, purchase_id):
        return dict(self._receipt) if self._receipt else None

    def events_for(self, mandate_jti):
        return tuple(dict(e) for e in self._events)

    def chain_verdict(self, mandate_jti):
        return self._chain

    def latest_checkpoint(self, mandate_jti):
        return dict(self._checkpoint) if self._checkpoint else None


def build(fake=None) -> EvidenceService:
    fake = fake or FakeReaders()
    return EvidenceService(
        purchases=fake,
        mandates=fake,
        intents=fake,
        receipts=fake,
        ledger=fake,
        witness=fake,
    )


def test_happy_path_assembles_an_ok_pack():
    pack = build().assemble("purchase-jti-1", now=NOW)
    assert pack.integrity == INTEGRITY_OK
    assert pack.failure_reasons == ()
    assert pack.chain is not None and pack.chain.ok
    assert len(pack.ledger_events) == 3
    assert pack.root_checkpoint is not None
    assert pack.receipt == RECEIPT


def test_pack_digest_is_stable_across_assemblies():
    first = build().assemble("purchase-jti-1", now=NOW)
    second = build().assemble("purchase-jti-1", now=NOW)
    assert first.digest == second.digest


def test_canonical_envelope_is_valid_json_and_carries_integrity():
    pack = build().assemble("purchase-jti-1", now=NOW)
    envelope = json.loads(pack_canonical_bytes(pack).decode("utf-8"))
    assert envelope["integrity"] == INTEGRITY_OK
    assert envelope["digest"] == pack.digest


def test_tampered_chain_fails_closed_with_reason():
    fake = FakeReaders(chain_ok=False, chain_reason="hash mismatch at seq 2")
    pack = build(fake).assemble("purchase-jti-1", now=NOW)
    assert pack.integrity == INTEGRITY_FAILED
    assert pack.failure_reasons == ("chain-failed:hash mismatch at seq 2",)
    # the pack is still returned — evidence never hides the failure
    assert pack.to_dict()["chain"]["reason"] == "hash mismatch at seq 2"


def test_missing_witness_and_missing_receipt_fail_with_reasons():
    fake = FakeReaders(receipt=None, checkpoint=None)
    pack = build(fake).assemble("purchase-jti-1", now=NOW)
    assert pack.integrity == INTEGRITY_FAILED
    assert "root-checkpoint-missing" in pack.failure_reasons


def test_missing_ledger_slice_fails():
    fake = FakeReaders(events=())
    pack = build(fake).assemble("purchase-jti-1", now=NOW)
    assert pack.integrity == INTEGRITY_FAILED
    assert "ledger-slice-empty" in pack.failure_reasons


def test_missing_decision_snapshot_fails():
    fake = FakeReaders(purchase={"purchase_id": "purchase-jti-1", "intent_jti": "jti-1"})
    pack = build(fake).assemble("purchase-jti-1", now=NOW)
    assert pack.integrity == INTEGRITY_FAILED
    assert "decision-missing" in pack.failure_reasons


def test_unknown_purchase_raises_for_http_404():
    with pytest.raises(EvidenceNotFoundError):
        build().assemble("purchase-nope", now=NOW)


def test_ok_pack_cannot_carry_failure_reasons():
    from src.api.evidence.models import EvidencePack

    with pytest.raises(ValueError, match="failed pack must list"):
        EvidencePack(
            purchase_id="p",
            mandate_jti="j",
            integrity=INTEGRITY_FAILED,
            generated_at=NOW,
            digest="d" * 64,
        )
