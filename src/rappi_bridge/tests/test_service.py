"""The guarded flow end-to-end against a fake merchant."""

import pytest
from src.rappi_bridge.config import BridgeConfig
from src.rappi_bridge.errors import (
    ApprovalInvalid,
    CapExceeded,
    Disabled,
    MinAmountRejected,
    PriceDrift,
    UncertainState,
)
from src.rappi_bridge.service import (
    CONFIRMED as STATE_CONFIRMED,
)
from src.rappi_bridge.service import compute_cart_hash

from .conftest import (
    ADDRESS_ID,
    FakeRappiClient,
    make_request,
    make_service,
    mint,
)


def live_config(**overrides) -> BridgeConfig:
    return BridgeConfig(dry_run=False, **overrides)


def test_dry_run_flow_never_clicks(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, BridgeConfig(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=True)
    receipt = service.place_order(
        make_request(cart_hash=cart_hash, token=token)
    )
    assert receipt["state"] == "dry_run_confirmed"
    assert receipt["dry_run"] is True
    assert receipt["order_id"] is None
    assert fake.place_calls == 0


def test_live_flow_clicks_once_and_confirms(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, live_config(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    receipt = service.place_order(
        make_request(cart_hash=cart_hash, token=token)
    )
    assert receipt["state"] == STATE_CONFIRMED
    assert receipt["order_id"] == "2496728264"
    assert fake.place_calls == 1


def test_replay_returns_original_receipt_without_reclick(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, live_config(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    request = make_request(cart_hash=cart_hash, token=token)
    first = service.place_order(request)
    second = service.place_order(request)
    assert first == second
    assert fake.place_calls == 1


def test_cart_drift_aborts_before_click(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, live_config(), fake, keys)
    stale_hash = "c" * 64  # quote from a cart that no longer matches
    token = mint(key, kid, cart_hash=stale_hash, dry_run=False)
    with pytest.raises(PriceDrift):
        service.place_order(make_request(cart_hash=stale_hash, token=token))
    assert fake.place_calls == 0


def test_cap_refuses_even_with_valid_token(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient(total="60000.00")
    service = make_service(
        tmp_path, live_config(max_order_cop="50000.00"), fake, keys
    )
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, amount="60000.00", dry_run=False)
    with pytest.raises(CapExceeded):
        service.place_order(make_request(cart_hash=cart_hash, amount="60000.00", token=token))
    assert fake.place_calls == 0


def test_address_mismatch_aborts(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, live_config(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    with pytest.raises(Exception) as excinfo:
        service.place_order(
            make_request(
                cart_hash=cart_hash,
                token=token,
                expected_address_id="197411600",
            )
        )
    assert getattr(excinfo.value, "reason", "") == "BRIDGE_ADDRESS_MISMATCH"
    assert fake.place_calls == 0


def test_uncertain_never_reclicks(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient(place_mode="boom")
    service = make_service(tmp_path, live_config(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    with pytest.raises(UncertainState):
        service.place_order(make_request(cart_hash=cart_hash, token=token))
    row = service.order_status("idem-1")
    assert row["state"] == "uncertain"
    with pytest.raises(UncertainState):
        service.place_order(make_request(cart_hash=cart_hash, token=token))
    assert fake.place_calls == 1  # the retry was refused


def test_min_amount_is_retryable(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient(place_mode="min")
    service = make_service(tmp_path, live_config(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    with pytest.raises(MinAmountRejected):
        service.place_order(make_request(cart_hash=cart_hash, token=token))
    assert service.order_status("idem-1")["state"] == "failed"
    # store adds one more product to cross the minimum; retry is allowed
    fake.place_mode = "ok"
    fake.products = fake.products + [
        {"product_id": 2, "name": "Galleta", "units": 1, "total": "2500.00"}
    ]
    fake.total = "22300.00"  # new cart => new quote & token
    new_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    new_token = mint(key, kid, cart_hash=new_hash, amount="22300.00", dry_run=False)
    receipt = service.place_order(
        make_request(cart_hash=new_hash, amount="22300.00", token=new_token)
    )
    assert receipt["state"] == STATE_CONFIRMED


def test_kill_switch_blocks_everything(tmp_path, kernel_key, keys) -> None:
    fake = FakeRappiClient()
    service = make_service(tmp_path, BridgeConfig(enabled=False), fake, keys)
    with pytest.raises(Disabled):
        service.place_order(make_request())
    with pytest.raises(Disabled):
        service.quote()


def test_dry_run_config_vs_token_mismatch_rejected(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, live_config(), fake, keys)  # live bridge
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=True)  # dry-run kernel
    with pytest.raises(ApprovalInvalid):
        service.place_order(make_request(cart_hash=cart_hash, token=token))
    assert fake.place_calls == 0


def test_quote_requires_clean_cart(tmp_path, kernel_key, keys) -> None:
    fake = FakeRappiClient()
    fake.get_carts = lambda: [  # type: ignore[method-assign]
        {
            "store_type": "restaurant",
            "stores": [{"name": "Turbo", "products": [{"product_id": 1}]}],
        }
    ]
    service = make_service(tmp_path, BridgeConfig(), fake, keys)
    from src.rappi_bridge.errors import CartNotClean

    with pytest.raises(CartNotClean):
        service.quote()


def test_address_id_flow_matches_fake(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, live_config(), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    receipt = service.place_order(
        make_request(
            cart_hash=cart_hash,
            token=token,
            expected_address_id=str(ADDRESS_ID),
        )
    )
    assert receipt["state"] == STATE_CONFIRMED


def test_request_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    request = make_request()
    with pytest.raises(FrozenInstanceError):
        request.amount = "1.00"  # type: ignore[misc]
