"""Guarded order flow (decision 0030 §4.1/§4.3).

Sequence: single-flight claim → session preflight → cart binding
(recomputed cart_hash) → capture-token verification → guards (cap, address,
drift) → armed → dry-run no-op or the paying click. A transport failure
after the click parks the order in `uncertain` and refuses to re-click.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.api.canonical import canonical_json, sha256_hex

from .config import BridgeConfig
from .errors import (
    Disabled,
    ExecutionConflict,
    MinAmountRejected,
    PriceDrift,
    RappiError,
    UncertainState,
)
from .guard import check_address, check_cap, check_cart_clean, check_drift, parse_money
from .rappi import RappiClient
from .state import (
    APPROVAL_VERIFIED,
    ARMED,
    CART_OK,
    CLICKED,
    CONFIRMED,
    DRY_RUN_CONFIRMED,
    FAILED,
    SESSION_OK,
    TERMINAL_OK,
    UNCERTAIN,
    BridgeState,
)
from .token import verify_capture_token


@dataclass(frozen=True, slots=True)
class PlaceOrderRequest:
    idem_key: str
    purchase_id: str
    amount: str
    cart_hash: str
    capture_token: str
    expected_address_id: str | None = None
    store_type: str = "restaurant"


@dataclass(frozen=True, slots=True)
class Quote:
    quote_id: str
    store_type: str
    store_name: str | None
    currency: str
    total: str
    cart_hash: str
    items: list[dict[str, Any]]
    delivery_address_id: str | None
    return_key: str | None
    quoted_at: str
    expires_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "merchant_id": "rappi",
            "store_type": self.store_type,
            "store_name": self.store_name,
            "currency": self.currency,
            "total": self.total,
            "cart_hash": self.cart_hash,
            "items": self.items,
            "delivery_address_id": self.delivery_address_id,
            "return_key": self.return_key,
            "quoted_at": self.quoted_at,
            "expires_at": self.expires_at,
        }


KeysProvider = dict[str, Any] | Callable[[], dict[str, Any]]


def compute_cart_hash(store_type: str, store: dict[str, Any]) -> str:
    """Stable binding of the quoted cart (post store/address resolution).
    Volatile checkout fields (ETA, timestamps) are deliberately excluded."""
    return sha256_hex(
        canonical_json(
            {
                "store_type": store_type,
                "store_id": store.get("id"),
                "products": store.get("products", []),
                "total": str(parse_money(str(store.get("total", "0")))),
            }
        )
    )


class BridgeService:
    def __init__(
        self,
        config: BridgeConfig,
        client: RappiClient,
        state: BridgeState,
        keys: KeysProvider,
    ) -> None:
        self._config = config
        self._client = client
        self._state = state
        self._keys = keys

    def order_status(self, idem_key: str) -> dict[str, Any] | None:
        return self._state.get(idem_key)

    def search(self, query: str) -> list[dict[str, Any]]:
        """Read-only catalog search; requires a live session."""
        if not self._config.enabled:
            raise Disabled("bridge is disabled (kill switch)")
        return self._client.search(query)

    # -- helpers -----------------------------------------------------------

    def _kernel_keys(self) -> dict[str, Any]:
        if callable(self._keys):
            return self._keys()
        return self._keys

    def _receipt(
        self,
        *,
        state: str,
        purchase_id: str,
        cart_hash: str,
        total: str,
        order_id: str | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "dry_run": dry_run,
            "order_id": order_id,
            "total_captured": total,
            "cart_hash": cart_hash,
            "purchase_id": purchase_id,
        }

    # -- read-side ---------------------------------------------------------

    def preflight(self) -> dict[str, Any]:
        user = self._client.whoami()
        address = self._client.active_address()
        return {
            "ok": True,
            "account_label": str(user.get("name", "unknown"))[:32],
            "address_active": {
                "id": str(address.get("id")) if address else None,
                "label": (address or {}).get("tag")
                or (address or {}).get("address", ""),
            },
        }

    def quote(
        self, store_type: str = "restaurant", *, require_clean_cart: bool = True
    ) -> Quote:
        """Canonical quote from the CURRENT cart, post store/address
        resolution. Search results are never a price source."""
        if not self._config.enabled:
            raise Disabled("bridge is disabled (kill switch)")
        self._client.whoami()
        carts = self._client.get_carts()
        if require_clean_cart:
            check_cart_clean(carts)
        resolved = self._client.resolve_store_type(store_type)
        cart = self._client.recalculate(resolved)
        stores = cart.get("stores") or []
        if not stores:
            raise RappiError("cart is empty — nothing to quote")
        store = stores[0]
        items = [
            {
                "sku": str(product.get("product_id") or product.get("id")),
                "title": str(product.get("name", "")),
                "qty": int(product.get("units", 1)),
                "unit_amount": str(
                    (
                        parse_money(str(product.get("total", "0")))
                        / max(int(product.get("units", 1)), 1)
                    ).quantize(Decimal("0.01"))
                ),
            }
            for product in store.get("products", [])
        ]
        total = parse_money(str(store.get("total", "0")))
        detail = self._client.checkout_detail(resolved)
        cart_hash = compute_cart_hash(resolved, store)
        address = self._client.active_address()
        now = datetime.now(UTC)
        return Quote(
            quote_id=f"rappi-quote-{uuid.uuid4()}",
            store_type=resolved,
            store_name=store.get("name"),
            currency="COP",
            total=str(total),
            cart_hash=cart_hash,
            items=items,
            delivery_address_id=(str(address.get("id")) if address else None),
            return_key=(detail or {}).get("return_key"),
            quoted_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self._config.quote_ttl_s)).isoformat(),
        )

    # -- the guarded click --------------------------------------------------

    def place_order(self, request: PlaceOrderRequest) -> dict[str, Any]:
        if not self._config.enabled:
            raise Disabled("bridge is disabled (kill switch)")
        approved = parse_money(request.amount)
        existing = self._state.claim(
            request.idem_key,
            purchase_id=request.purchase_id,
            cart_hash=request.cart_hash,
            amount=str(approved),
        )
        if existing is not None:
            state = existing["state"]
            if state in TERMINAL_OK and existing["receipt"]:
                return existing["receipt"]  # replay: original receipt, no re-click
            if state in (CLICKED, UNCERTAIN):
                raise UncertainState(
                    "order was clicked without confirmation; human "
                    "reconciliation required — never re-click",
                    detail={"idem_key": request.idem_key},
                )
            # failed(pre-capture) / partial checkpoints: safe to re-run steps

        # 1. session
        self._client.whoami()
        self._state.transition(request.idem_key, SESSION_OK)

        # 2. cart binding: the cart NOW must hash to the approved cart_hash
        resolved = self._client.resolve_store_type(request.store_type)
        cart = self._client.recalculate(resolved)
        stores = cart.get("stores") or []
        if not stores:
            raise PriceDrift(
                "cart is empty but an amount was approved",
                detail={"approved": str(approved)},
            )
        store = stores[0]
        checkout_total = parse_money(str(store.get("total", "0")))
        current_hash = compute_cart_hash(resolved, store)
        if current_hash != request.cart_hash:
            raise PriceDrift(
                "cart changed since the approved quote — re-quote required",
                detail={
                    "approved_cart_hash": request.cart_hash,
                    "current": current_hash,
                },
            )
        self._state.transition(request.idem_key, CART_OK)

        # 3. capture token: the human approval is the key that unlocks the click
        claims = verify_capture_token(
            request.capture_token,
            keys=self._kernel_keys(),
            expected_purchase_id=request.purchase_id,
            expected_cart_hash=request.cart_hash,
            expected_amount=str(approved),
            expected_dry_run=self._config.dry_run,
        )
        self._state.transition(request.idem_key, APPROVAL_VERIFIED)

        # 4. guards — independent of the kernel, enforced on this machine
        check_cap(checkout_total, self._config.cap)
        detail = self._client.checkout_detail(resolved)
        check_drift(approved, checkout_total)
        address = self._client.active_address()
        check_address(
            str(address.get("id")) if address else None,
            request.expected_address_id,
        )
        self._state.transition(request.idem_key, ARMED)

        # 5. the click — the only money-moving statement in the codebase
        if self._config.dry_run and not claims.get("dry_run"):
            raise Disabled("live click attempted while bridge is in DRY_RUN")

        def _finish(state: str, order_id: str | None) -> dict[str, Any]:
            receipt = self._receipt(
                state=state,
                purchase_id=request.purchase_id,
                cart_hash=request.cart_hash,
                total=str(checkout_total),
                order_id=order_id,
                dry_run=self._config.dry_run,
            )
            self._state.transition(
                request.idem_key, state, order_id=order_id, receipt=receipt
            )
            return receipt

        if self._config.dry_run:
            return _finish(DRY_RUN_CONFIRMED, None)

        try:
            # Optimistic guard: exactly one worker may walk ARMED -> CLICKED.
            self._state.transition(request.idem_key, CLICKED, expect=ARMED)
        except RuntimeError as exc:
            raise ExecutionConflict(
                "another execution is already holding this order",
                detail={"idem_key": request.idem_key},
            ) from exc
        try:
            result = self._client.place_order(
                resolved, return_key=str((detail or {}).get("return_key") or "")
            )
        except MinAmountRejected as exc:
            # Rappi rejected pre-capture (store minimum): no order exists,
            # nothing moved — the machine allows a safe retry (failed).
            self._state.transition(request.idem_key, FAILED, expect=CLICKED)
            raise exc
        except Exception as exc:
            # Post-click ambiguity: park as uncertain; NEVER re-click.
            self._state.transition(request.idem_key, UNCERTAIN, expect=CLICKED)
            raise UncertainState(
                f"click result unknown ({exc}); reconcile via 'Mis pedidos'",
                detail={"idem_key": request.idem_key},
            ) from exc
        order_id = str((result or {}).get("order_id") or (result or {}).get("id") or "")
        return _finish(CONFIRMED, order_id or None)
