"""Shared fixtures: fake Rappi client, service factory, token minter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from src.api.decision.capture_token import mint_capture_token
from src.rappi_bridge.config import BridgeConfig
from src.rappi_bridge.errors import MinAmountRejected, RappiError
from src.rappi_bridge.rappi import RappiClient
from src.rappi_bridge.service import BridgeService, PlaceOrderRequest, compute_cart_hash
from src.rappi_bridge.state import BridgeState
from src.trustlib.jose import generate_ed25519, public_jwk

ADDRESS_ID = 1125328637
STORE_ID = 900139848
PRODUCTS = [
    {"product_id": 3522980, "name": "Pringles Original (grande)", "units": 1, "total": "18300.00"}
]
TOTAL = "18300.00"


class FakeRappiClient(RappiClient):
    """Duck-typed stand-in: never touches the network."""

    def __init__(
        self,
        *,
        products: list[dict[str, Any]] | None = None,
        total: str = TOTAL,
        place_mode: str = "ok",
    ) -> None:
        self.products = products if products is not None else PRODUCTS
        self.total = total
        self.place_mode = place_mode
        self.place_calls = 0
        self.whoami_calls = 0

    def whoami(self) -> dict[str, Any]:
        self.whoami_calls += 1
        return {"name": "Test Owner", "id": 1, "email": "owner@example.com"}

    def addresses(self) -> dict[str, Any]:
        return {"addresses": [{"id": ADDRESS_ID, "active": True, "tag": "Casa"}]}

    def active_address(self) -> dict[str, Any] | None:
        return {"id": ADDRESS_ID, "tag": "Casa", "address": "Cl. 40B"}

    def get_carts(self) -> list[dict[str, Any]]:
        return [{"store_type": "restaurant", "store_type_origin": "restaurant", "stores": []}]

    def resolve_store_type(self, preferred: str) -> str:
        return "restaurant"

    def recalculate(self, store_type: str) -> dict[str, Any]:
        return {
            "stores": [
                {
                    "id": STORE_ID,
                    "name": "Turbo Parque Bavaria",
                    "total": self.total,
                    "products": self.products,
                }
            ]
        }

    def checkout_detail(self, store_type: str) -> dict[str, Any]:
        return {"return_key": "rk-123", "totals": {"total": self.total}}

    def orders(self) -> dict[str, Any]:
        return {"orders": []}

    def add_to_cart(self, store_type: str, stores_payload: list) -> dict[str, Any]:
        return {"ok": True}

    def place_order(self, store_type: str, *, return_key: str) -> dict[str, Any]:
        self.place_calls += 1
        if self.place_mode == "min":
            raise MinAmountRejected("store minimum not met")
        if self.place_mode == "boom":
            raise RappiError("transport died mid-click")
        return {"order_id": "2496728264", "state": "created"}


@pytest.fixture()
def kernel_key() -> tuple[Any, str]:
    key = generate_ed25519()
    return key, "kernel-test-kid"


@pytest.fixture()
def keys(kernel_key: tuple[Any, str]) -> dict[str, Any]:
    key, kid = kernel_key
    return {kid: key}


def mint(
    key: Any,
    kid: str,
    *,
    purchase_id: str = "purchase-test-1",
    amount: str = TOTAL,
    cart_hash: str | None = None,
    dry_run: bool = True,
    ttl_seconds: int = 120,
    now: datetime | None = None,
) -> str:
    return mint_capture_token(
        purchase_id=purchase_id,
        reservation_id="reservation-1",
        amount=amount,
        cart_hash=cart_hash or "0" * 64,
        key=key,
        kid=kid,
        ttl_seconds=ttl_seconds,
        dry_run=dry_run,
        now=now,
    )


def make_service(
    tmp_path: Any,
    config: BridgeConfig,
    fake: FakeRappiClient,
    keys: dict[str, Any],
) -> BridgeService:
    state = BridgeState(tmp_path / "state.sqlite3")
    return BridgeService(config, fake, state, keys)  # type: ignore[arg-type]


def make_request(
    *,
    cart_hash: str | None = None,
    amount: str = TOTAL,
    token: str = "token",
    purchase_id: str = "purchase-test-1",
    expected_address_id: str | None = None,
) -> PlaceOrderRequest:
    return PlaceOrderRequest(
        idem_key="idem-1",
        purchase_id=purchase_id,
        amount=amount,
        cart_hash=cart_hash or "0" * 64,
        capture_token=token,
        expected_address_id=expected_address_id,
    )


def now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ADDRESS_ID",
    "FakeRappiClient",
    "PRODUCTS",
    "STORE_ID",
    "TOTAL",
    "compute_cart_hash",
    "make_request",
    "make_service",
    "mint",
    "now_utc",
    "public_jwk",
]
