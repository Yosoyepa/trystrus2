"""Capture-token roundtrip: mint (kernel side) <-> verify (bridge side)."""

from datetime import UTC, datetime, timedelta

import pytest
from src.rappi_bridge.errors import ApprovalExpired, ApprovalInvalid
from src.rappi_bridge.token import verify_capture_token

from .conftest import mint


def test_roundtrip_ok(kernel_key, keys) -> None:
    key, kid = kernel_key
    token = mint(key, kid, cart_hash="a" * 64, dry_run=True)
    claims = verify_capture_token(
        token,
        keys=keys,
        expected_purchase_id="purchase-test-1",
        expected_cart_hash="a" * 64,
        expected_amount="18300.00",
        expected_dry_run=True,
    )
    assert claims["purchase_id"] == "purchase-test-1"
    assert claims["reservation_id"] == "reservation-1"
    assert claims["dry_run"] is True


def test_expired_token_rejected(kernel_key, keys) -> None:
    key, kid = kernel_key
    token = mint(key, kid, ttl_seconds=1)
    later = datetime.now(UTC) + timedelta(seconds=30)
    with pytest.raises(ApprovalExpired):
        verify_capture_token(
            token,
            keys=keys,
            expected_purchase_id="purchase-test-1",
            expected_cart_hash="0" * 64,
            expected_amount="18300.00",
            expected_dry_run=True,
            now=later,
        )


def test_wrong_cart_hash_rejected(kernel_key, keys) -> None:
    key, kid = kernel_key
    token = mint(key, kid, cart_hash="a" * 64)
    with pytest.raises(ApprovalInvalid):
        verify_capture_token(
            token,
            keys=keys,
            expected_purchase_id="purchase-test-1",
            expected_cart_hash="b" * 64,
            expected_amount="18300.00",
            expected_dry_run=True,
        )


def test_amount_mismatch_rejected(kernel_key, keys) -> None:
    key, kid = kernel_key
    token = mint(key, kid)
    with pytest.raises(ApprovalInvalid):
        verify_capture_token(
            token,
            keys=keys,
            expected_purchase_id="purchase-test-1",
            expected_cart_hash="0" * 64,
            expected_amount="99999.00",
            expected_dry_run=True,
        )


def test_dry_run_flag_mismatch_rejected(kernel_key, keys) -> None:
    key, kid = kernel_key
    token = mint(key, kid, dry_run=True)
    with pytest.raises(ApprovalInvalid):
        verify_capture_token(
            token,
            keys=keys,
            expected_purchase_id="purchase-test-1",
            expected_cart_hash="0" * 64,
            expected_amount="18300.00",
            expected_dry_run=False,
        )


def test_wrong_key_rejected(kernel_key, keys) -> None:
    from src.trustlib.jose import generate_ed25519

    key, kid = kernel_key
    token = mint(key, kid)
    other = {kid: generate_ed25519()}
    with pytest.raises(ApprovalInvalid):
        verify_capture_token(
            token,
            keys=other,
            expected_purchase_id="purchase-test-1",
            expected_cart_hash="0" * 64,
            expected_amount="18300.00",
            expected_dry_run=True,
        )


def test_tampered_payload_rejected(kernel_key, keys) -> None:
    key, kid = kernel_key
    token = mint(key, kid)
    header, _, signature = token.split(".")
    forged = f"{header}.{token.split('.')[1] + 'Cg'}.{signature}"  # payload bits flipped
    with pytest.raises(ApprovalInvalid):
        verify_capture_token(
            forged,
            keys=keys,
            expected_purchase_id="purchase-test-1",
            expected_cart_hash="0" * 64,
            expected_amount="18300.00",
            expected_dry_run=True,
        )


def test_wrong_typ_rejected(kernel_key, keys) -> None:
    from src.trustlib.jose import sign_compact

    key, kid = kernel_key
    token = sign_compact({"purchase_id": "x"}, key, kid=kid, typ="application/jwt")
    with pytest.raises(ApprovalInvalid) as excinfo:
        verify_capture_token(
            token,
            keys=keys,
            expected_purchase_id="x",
            expected_cart_hash="0" * 64,
            expected_amount="18300.00",
            expected_dry_run=True,
        )
    assert "unexpected token typ" in str(excinfo.value)
