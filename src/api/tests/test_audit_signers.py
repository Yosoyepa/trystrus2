"""Tests for root signers (LocalEd25519, KMS) and external witness storage (InMemory, GCS)."""

import os
from datetime import UTC, datetime

import pytest
from src.api.audit.models import RootCheckpoint
from src.api.audit.signer_kms import KMSRootSigner
from src.api.audit.signer_local import LocalEd25519Signer
from src.api.audit.witness_gcs import GCSWitness
from src.api.audit.witness_memory import InMemoryWitness


class TestLocalSigner:
    def test_sign_and_verify_roundtrip(self) -> None:
        signer = LocalEd25519Signer.generate()
        data = b"canonical-audit-payload-to-sign"
        sig = signer.sign(data)

        assert len(sig) == 64
        assert signer.verify(data, sig) is True

    def test_verify_fails_on_corrupted_payload(self) -> None:
        signer = LocalEd25519Signer.generate()
        data = b"original-payload"
        sig = signer.sign(data)

        assert signer.verify(b"tampered-payload", sig) is False

    def test_verify_fails_on_corrupted_signature(self) -> None:
        signer = LocalEd25519Signer.generate()
        data = b"original-payload"
        sig = bytearray(signer.sign(data))
        sig[0] ^= 0xFF  # Flip bits

        assert signer.verify(data, bytes(sig)) is False

    def test_pem_export_and_import(self) -> None:
        signer = LocalEd25519Signer.generate()
        pem = signer.export_private_pem()
        reloaded = LocalEd25519Signer.from_pem(pem)

        data = b"test-pem-roundtrip"
        sig = signer.sign(data)
        assert reloaded.verify(data, sig) is True

    def test_invalid_input_types_fail_closed(self) -> None:
        signer = LocalEd25519Signer.generate()
        with pytest.raises(TypeError):
            signer.sign("not-bytes")  # type: ignore[arg-type]
        assert signer.verify("not-bytes", b"123") is False  # type: ignore[arg-type]


class TestInMemoryWitness:
    def test_put_and_get_checkpoint(self) -> None:
        witness = InMemoryWitness()
        cp = RootCheckpoint(
            seq_start=1,
            seq_end=10,
            root_hash="a" * 64,
            root_sig="sig_test_1_10",
            cardinality=10,
            created_at=datetime.now(UTC),
        )
        witness.put(cp)

        retrieved = witness.get(1, 10)
        assert retrieved is not None
        assert retrieved.seq_start == 1
        assert retrieved.seq_end == 10
        assert retrieved.root_hash == "a" * 64
        assert retrieved.root_sig == "sig_test_1_10"

    def test_duplicate_put_raises_immutability_error(self) -> None:
        witness = InMemoryWitness()
        cp = RootCheckpoint(
            seq_start=1,
            seq_end=5,
            root_hash="b" * 64,
            root_sig="sig_test",
            cardinality=5,
        )
        witness.put(cp)
        with pytest.raises(ValueError, match="already exists"):
            witness.put(cp)

    def test_get_non_existent_range_returns_none(self) -> None:
        witness = InMemoryWitness()
        assert witness.get(100, 200) is None

    def test_tamper_and_delete_hooks(self) -> None:
        witness = InMemoryWitness()
        cp = RootCheckpoint(
            seq_start=1,
            seq_end=5,
            root_hash="c" * 64,
            root_sig="sig_c",
            cardinality=5,
        )
        witness.put(cp)

        # Tamper
        witness.tamper(1, 5, "root_hash", "d" * 64)
        retrieved = witness.get(1, 5)
        assert retrieved is not None
        assert retrieved.root_hash == "d" * 64

        # Delete
        witness.delete(1, 5)
        assert witness.get(1, 5) is None


@pytest.mark.gcp
class TestGCPIntegration:
    def test_kms_signer_live(self) -> None:
        key_resource = os.environ.get("AVAL_KMS_KEY_RESOURCE")
        if not key_resource:
            pytest.skip("AVAL_KMS_KEY_RESOURCE not set; skipping live KMS test")

        signer = KMSRootSigner(key_resource)
        payload = b"live-kms-test-payload"
        signature = signer.sign(payload)

        assert len(signature) == 64
        assert signer.verify(payload, signature) is True
        assert signer.verify(b"different-payload", signature) is False

    def test_gcs_witness_live(self) -> None:
        bucket_name = os.environ.get("AVAL_WITNESS_BUCKET")
        if not bucket_name:
            pytest.skip("AVAL_WITNESS_BUCKET not set; skipping live GCS test")

        witness = GCSWitness(bucket_name)
        cp = RootCheckpoint(
            seq_start=99901,
            seq_end=99910,
            root_hash="e" * 64,
            root_sig="kms_sig_live_test",
            cardinality=10,
        )
        witness.put(cp)
        retrieved = witness.get(99901, 99910)

        assert retrieved is not None
        assert retrieved.seq_start == 99901
        assert retrieved.root_hash == "e" * 64
