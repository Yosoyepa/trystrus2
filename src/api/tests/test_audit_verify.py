"""T9 — Audit hash chain and external witness verification test suite.

This test models the live judge demonstration:
"Flip one byte in the database, and verification breaks on stage with sequence identification."
"""

from decimal import Decimal

import pytest
from src.api.audit.repository_memory import InMemoryLedgerRepository
from src.api.audit.service import LedgerService
from src.api.audit.signer_local import LocalEd25519Signer
from src.api.audit.witness_memory import InMemoryWitness

LedgerSetup = tuple[
    LedgerService,
    InMemoryLedgerRepository,
    LocalEd25519Signer,
    InMemoryWitness,
]


@pytest.fixture
def ledger_setup() -> LedgerSetup:
    repo = InMemoryLedgerRepository()
    signer = LocalEd25519Signer.generate()
    witness = InMemoryWitness()
    service = LedgerService(repo, signer, witness)

    # Populate 25 events across mandates
    for i in range(1, 26):
        mandate_id = "mdt_01" if i % 2 == 1 else "mdt_02"
        event_type = "purchase.verified" if i % 3 == 0 else "purchase.requested"
        service.append(
            type=event_type,
            mandate_id=mandate_id,
            payload={
                "event_index": i,
                "amount": Decimal(f"{100 + i}.00"),
                "merchant": "vuelaya",
            },
        )

    # Create two signed checkpoints:
    # Checkpoint 1: events 1..10
    service.sign_root(1, 10)
    # Checkpoint 2: events 11..20
    service.sign_root(11, 20)
    # Events 21..25 remain uncheckpointed

    return service, repo, signer, witness


class TestT9ChainVerification:
    def test_t9_intact_chain_passes(self, ledger_setup: LedgerSetup) -> None:
        service, _, _, _ = ledger_setup
        result = service.verify_chain()

        assert result.ok is True
        assert result.verified_count == 25
        assert result.first_bad_seq is None
        assert result.last_hash is not None

    def test_t9_tamper_payload_breaks_stage(self, ledger_setup: LedgerSetup) -> None:
        """Demo script: alter 1 byte in payload of event seq=7 -> breaks at seq 7."""
        service, repo, _, _ = ledger_setup

        repo.tamper(7, "payload", {"event_index": 7, "amount": "9999.00", "merchant": "vuelaya"})
        result = service.verify_chain()

        assert result.ok is False
        assert result.first_bad_seq == 7
        assert "hash mismatch" in (result.reason or "").lower()

    def test_t9_tamper_hash_breaks_stage(self, ledger_setup: LedgerSetup) -> None:
        """Demo script: alter hash of event seq=15 -> breaks at seq 15."""
        service, repo, _, _ = ledger_setup

        repo.tamper(15, "hash", "f" * 64)
        result = service.verify_chain()

        assert result.ok is False
        assert result.first_bad_seq == 15
        assert "hash mismatch" in (result.reason or "").lower()

    def test_t9_corrupted_root_sig_fails(self, ledger_setup: LedgerSetup) -> None:
        """Tampering with root_sig fails cryptographic verification."""
        service, repo, _, _ = ledger_setup

        repo.tamper(3, "root_sig", "00" * 64)
        result = service.verify_chain()

        assert result.ok is False
        assert "root signature" in (result.reason or "").lower()

    def test_t9_divergent_witness_fails(self, ledger_setup: LedgerSetup) -> None:
        """External witness divergence turns tamper-evidence into accountability (decision #7)."""
        service, _, _, witness = ledger_setup

        # Alter root_hash in external witness for checkpoint 1..10
        witness.tamper(1, 10, "root_hash", "e" * 64)
        result = service.verify_chain()

        assert result.ok is False
        assert result.first_bad_seq == 1
        assert "external witness divergence" in (result.reason or "").lower()

    def test_t9_missing_witness_fails(self, ledger_setup: LedgerSetup) -> None:
        """Missing external witness fails closed."""
        service, _, _, witness = ledger_setup

        # Delete checkpoint 11..20 from external witness
        witness.delete(11, 20)
        result = service.verify_chain()

        assert result.ok is False
        assert result.first_bad_seq == 11
        assert "missing external witness" in (result.reason or "").lower()

    def test_t9_mandate_projection_verification(self, ledger_setup: LedgerSetup) -> None:
        """Verify chain scoped to a single mandate."""
        service, repo, _, _ = ledger_setup

        res_m1 = service.verify_chain(mandate_id="mdt_01")
        assert res_m1.ok is True
        assert res_m1.verified_count == 13  # Odd numbers 1..25

        # Tamper an event of mdt_01
        repo.tamper(5, "payload", {"tampered": True})
        res_m1_tampered = service.verify_chain(mandate_id="mdt_01")
        assert res_m1_tampered.ok is False
        assert res_m1_tampered.first_bad_seq == 5
