"""Pure guards: cap, cent-exact drift, address binding, clean cart."""

import pytest
from src.rappi_bridge.errors import (
    AddressMismatch,
    CapExceeded,
    CartNotClean,
    PriceDrift,
)
from src.rappi_bridge.guard import (
    check_address,
    check_cap,
    check_cart_clean,
    check_drift,
    parse_money,
)
from src.rappi_bridge.service import compute_cart_hash

from .conftest import STORE_ID, TOTAL


def test_cap_boundary() -> None:
    cap = parse_money("50000.00")
    check_cap(parse_money("50000.00"), cap)
    with pytest.raises(CapExceeded):
        check_cap(parse_money("50000.01"), cap)


def test_drift_is_cent_exact() -> None:
    approved = parse_money(TOTAL)
    check_drift(approved, parse_money(TOTAL))
    with pytest.raises(PriceDrift):
        check_drift(approved, parse_money("18300.01"))


def test_address_binding() -> None:
    check_address("1125328637", "1125328637")
    check_address("1125328637", None)  # mandate without address binding
    with pytest.raises(AddressMismatch):
        check_address("197411600", "1125328637")


def test_clean_cart() -> None:
    check_cart_clean([{"store_type": "restaurant", "stores": []}])
    dirty = [
        {
            "store_type": "restaurant",
            "stores": [{"name": "Turbo", "products": [{"product_id": 1}]}],
        }
    ]
    with pytest.raises(CartNotClean):
        check_cart_clean(dirty)


def test_cart_hash_is_stable_and_sensitive() -> None:
    store = {"id": STORE_ID, "total": TOTAL, "products": [{"product_id": 1, "units": 1}]}
    other_price = dict(store, total="18301.00")
    assert compute_cart_hash("restaurant", store) == compute_cart_hash("restaurant", store)
    assert compute_cart_hash("restaurant", store) != compute_cart_hash("restaurant", other_price)


def test_cart_hash_normalises_rappi_float_numbers() -> None:
    numeric = {
        "id": STORE_ID,
        "total": 1725.0,
        "products": [{"product_id": 1, "units": 1.0, "total": 1725.0}],
    }
    strings = {
        "id": STORE_ID,
        "total": "1725",
        "products": [{"product_id": "1", "units": "1", "total": "1725"}],
    }

    assert compute_cart_hash("restaurant", numeric) == compute_cart_hash("restaurant", strings)
