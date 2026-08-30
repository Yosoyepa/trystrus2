"""Payment-method integration (DanidiazTech fork 6a20e1e): the silent cash
fallback is the failure mode this bridge must never reproduce."""

from src.rappi_bridge.config import BridgeConfig
from src.rappi_bridge.errors import CardRequires3ds, CashPaymentRefused, PaymentUnresolved
from src.rappi_bridge.rappi import build_payment_payload, method_is_cash
from src.rappi_bridge.service import compute_cart_hash

from .conftest import FakeRappiClient, make_request, make_service, mint


def live(tmp_path, fake, keys, **config) -> tuple:
    from .conftest import make_service as _make

    return _make(tmp_path, BridgeConfig(dry_run=False, **config), fake, keys)


def test_payload_mirrors_resolver_and_drops_empties() -> None:
    method = FakeRappiClient.CARD_METHOD
    payload = build_payment_payload(method)
    assert payload["payment_method_type"] == "cc|10101063546022"
    assert payload["card"] == {"card_reference": "10101063546022"}
    charge = payload["charge_data"]
    assert charge["last_four_digits"] == "4321"
    assert charge["origin_platform"] == "web"
    assert "threeds_reference_id" not in charge  # empty values are dropped
    assert "language" not in charge


def test_cash_detection() -> None:
    assert method_is_cash(FakeRappiClient.CASH_METHOD)
    assert not method_is_cash(FakeRappiClient.CARD_METHOD)


def test_default_card_applied_before_click(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, BridgeConfig(dry_run=False), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    receipt = service.place_order(make_request(cart_hash=cart_hash, token=token))
    assert receipt["state"] == "confirmed"
    assert fake.payment_payloads, "payment method must be pushed to the cart"
    assert fake.payment_payloads[0]["payment_method_type"] == "cc|10101063546022"
    assert fake.place_calls == 1


def test_cash_only_account_is_refused(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient(payment_methods=[dict(FakeRappiClient.CASH_METHOD)])
    service = make_service(tmp_path, BridgeConfig(dry_run=False), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    try:
        service.place_order(make_request(cart_hash=cart_hash, token=token))
        raise AssertionError("cash must be refused")
    except CashPaymentRefused as exc:
        assert exc.reason == "BRIDGE_CASH_NOT_ALLOWED"
    assert fake.place_calls == 0


def test_missing_preferred_method_fails_closed(tmp_path, kernel_key, keys) -> None:
    """A mandate names its instrument: silently charging another card is
    exactly the failure the resolver knowledge exists to prevent."""
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(
        tmp_path,
        BridgeConfig(dry_run=False, payment_method_id="cc|00000000000000"),
        fake,
        keys,
    )
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    try:
        service.place_order(make_request(cart_hash=cart_hash, token=token))
        raise AssertionError("missing preferred method must fail closed")
    except PaymentUnresolved:
        pass
    assert fake.place_calls == 0


def test_explicit_cash_allowed_when_operator_opts_in(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient(payment_methods=[dict(FakeRappiClient.CASH_METHOD)])
    service = make_service(tmp_path, BridgeConfig(dry_run=False, allow_cash=True), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    receipt = service.place_order(make_request(cart_hash=cart_hash, token=token))
    assert receipt["payment_method"] == "Efectivo"
    assert fake.place_calls == 1


def test_receipt_carries_payment_method(tmp_path, kernel_key, keys) -> None:
    key, kid = kernel_key
    fake = FakeRappiClient()
    service = make_service(tmp_path, BridgeConfig(dry_run=True), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=True)
    receipt = service.place_order(make_request(cart_hash=cart_hash, token=token))
    assert receipt["payment_method"] == "Visa ••4321"


def test_fraud_flagged_3ds_card_refused(tmp_path, kernel_key, keys) -> None:

    key, kid = kernel_key
    flagged = dict(FakeRappiClient.CARD_METHOD)
    charge = dict(FakeRappiClient.CARD_METHOD["metadata"]["charge_data"])
    charge["tags"] = "require_3ds_by_fraud"
    flagged["metadata"] = {"charge_data": charge}
    fake = FakeRappiClient(payment_methods=[flagged, dict(FakeRappiClient.CASH_METHOD)])
    service = make_service(tmp_path, BridgeConfig(dry_run=False), fake, keys)
    cart_hash = compute_cart_hash("restaurant", fake.recalculate("restaurant")["stores"][0])
    token = mint(key, kid, cart_hash=cart_hash, dry_run=False)
    try:
        service.place_order(make_request(cart_hash=cart_hash, token=token))
        raise AssertionError("3ds-flagged card must be refused")
    except CardRequires3ds as exc:
        assert exc.reason == "BRIDGE_CARD_3DS_REQUIRED"
    assert fake.place_calls == 0


def test_three_ds_flag_helper() -> None:
    from src.rappi_bridge.rappi import method_needs_3ds

    flagged = {
        "id": "cc|1",
        "payment_method_tags": ["REQUIRE_3DS"],
        "metadata": {"charge_data": {}},
    }
    clean = {"id": "cc|2", "metadata": {"charge_data": {"payment_method": "cc"}}}
    assert method_needs_3ds(flagged)
    assert not method_needs_3ds(clean)
