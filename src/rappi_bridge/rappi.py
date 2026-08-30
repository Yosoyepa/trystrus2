"""Native httpx client for the Rappi web-internal API.

Endpoints ported from the audited `@crafter/rappi-cli` (decision 0030) — the
same calls the Rappi web app makes, with the web microfrontend headers. No
authentication beyond the session token captured by the owner's own login.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .errors import MinAmountRejected, RappiError, SessionExpired

AUTH_PATH = "/ms/application-user/auth"
ADDRESSES_PATH = "/api/ms/users-address/addresses"
CARTS_PATH = "/api/ms/shopping-cart/v1/all/get"
CART_PUT_PATH = "/api/ms/shopping-cart/v2/{store_type}/store"
RECALC_PATH = "/api/ms/shopping-cart/v1/{store_type}/recalculate"
CHECKOUT_DETAIL_PATH = "/api/ms/shopping-cart/v1/{store_type}/checkout/detail"
PLACE_ORDER_PATH = "/api/ms/shopping-cart-proxy/{store_type}/checkout"
PAYMENT_RESOLVER_PATH = "/api/ms/payment-method/resolver/v6"
PAYMENT_PUT_PATH = "/api/ms/shopping-cart/v1/{store_type}/payment-method"
ORDERS_PATH = "/api/user-order-home/orders"

# The web build hash rotates with Rappi's frontend deploys; if requests start
# failing en masse, refresh from a live browser session (smoke test F0).
_APP_VERSION = "e1de6be43aa29091011474615d7ac0810051c36a"
_BASE_HEADERS = {
    "accept": "application/json",
    "accept-language": "es-CO",
    "app-version": _APP_VERSION,
    "needappsflyerid": "false",
    "origin": "https://www.rappi.com.co",
    "referer": "https://www.rappi.com.co/",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile "
        "Safari/537.36"
    ),
    "vendor": "rappi",
    "x-application-id": f"rappi-microfront-web/{_APP_VERSION}",
}


@dataclass(frozen=True, slots=True)
class RappiSession:
    token: str
    device_id: str
    lat: str
    lng: str


def load_session(path: Path) -> RappiSession:
    """Read the session file written by the CLI login (never a password)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    token = data.get("token")
    if not token:
        raise SessionExpired(f"session file {path} has no token; re-login")
    return RappiSession(
        token=token,
        device_id=data.get("deviceId") or data.get("device_id") or "aval-bridge",
        lat=str(data.get("lat", "")),
        lng=str(data.get("lng", "")),
    )


class RappiClient:
    """Thin typed wrapper; every call raises on non-2xx."""

    def __init__(
        self,
        session: RappiSession,
        *,
        base_url: str = "https://services.grability.rappi.com",
        timeout_s: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._session = session
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        return {
            **_BASE_HEADERS,
            "authorization": f"Bearer {self._session.token}",
            "deviceid": self._session.device_id,
        }

    def _request(
        self, method: str, path: str, *, params: Any = None, body: Any = None
    ) -> Any:
        try:
            response = self._client.request(
                method, path, params=params, json=body
            )
        except httpx.HTTPError as exc:
            raise RappiError(f"rappi transport error on {path}: {exc}") from exc
        if response.status_code in (401, 403):
            raise SessionExpired(f"rappi rejected the session ({path})")
        if response.status_code >= 400:
            snippet = response.text[:200]
            lowered = snippet.lower()
            if "min_amount" in lowered or "mínimo" in lowered or "minimo" in lowered:
                raise MinAmountRejected(
                    f"store minimum not met on {path}", detail={"body": snippet}
                )
            raise RappiError(
                f"rappi {method} {path} -> {response.status_code}",
                detail={"body": snippet},
            )
        if not response.content:
            return {}
        return response.json()

    # -- read-side ---------------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", AUTH_PATH)

    def addresses(self) -> dict[str, Any]:
        return self._request("GET", ADDRESSES_PATH)

    def active_address(self) -> dict[str, Any] | None:
        data = self.addresses()
        for address in data.get("addresses", []):
            if address.get("active"):
                return address
        return None

    def get_carts(self) -> list[dict[str, Any]]:
        data = self._request("POST", CARTS_PATH, body={})
        return data if isinstance(data, list) else data.get("carts", [])

    def resolve_store_type(self, preferred: str) -> str:
        """turbo and friends are stored as `restaurant` server-side."""
        for cart in self.get_carts():
            if cart.get("store_type") == preferred:
                return cart.get("store_type_origin") or preferred
        return preferred

    def recalculate(self, store_type: str) -> dict[str, Any]:
        return self._request(
            "POST", RECALC_PATH.format(store_type=store_type), body={}
        )

    def checkout_detail(self, store_type: str) -> dict[str, Any]:
        return self._request("GET", CHECKOUT_DETAIL_PATH.format(store_type=store_type))

    def orders(self) -> dict[str, Any]:
        return self._request("GET", ORDERS_PATH)

    # -- write-side --------------------------------------------------------

    def add_to_cart(
        self, store_type: str, stores_payload: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """PUT replaces the store contents (DELETE /product is broken: 404)."""
        return self._request(
            "PUT",
            CART_PUT_PATH.format(store_type=store_type),
            body=stores_payload,
        )

    def place_order(self, store_type: str, *, return_key: str) -> dict[str, Any]:
        """THE click. Charges the payment method already selected on the
        account (the vaulted card). Never call outside the guarded flow."""
        return self._request(
            "POST",
            PLACE_ORDER_PATH.format(store_type=store_type),
            body={"return_key": return_key},
        )


class LazyRappiClient:
    """Duck-typed RappiClient that (re)loads the session file on demand.

    Lets the bridge start with NO session (status: idle) and picks up the
    fresh token as soon as the Config Rappi login writes it.
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._inner: RappiClient | None = None
        self._mtime: int | None = None

    def _ensure(self) -> RappiClient:
        path = self._config.session_file
        if not path.exists():
            raise SessionExpired("no Rappi session — use Config Rappi login")
        mtime = path.stat().st_mtime_ns
        if self._inner is None or mtime != self._mtime:
            self._inner = RappiClient(
                load_session(path),
                base_url=self._config.rappi_base_url,
                timeout_s=self._config.http_timeout_s,
            )
            self._mtime = mtime
        return self._inner

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)  # pickling/copy protocols stay local
        return getattr(self._ensure(), name)

    def search(self, query: str) -> list[dict[str, Any]]:
        """Unified catalog search (read-only). POST per the audited CLI."""
        data = self._request(
            "POST",
            "/api/pns-global-search-api/v1/unified-search?is_prime=false",
            body={"query": query, "lat": self._session.lat, "lng": self._session.lng},
        )
        results: list[dict[str, Any]] = []
        for store in data.get("stores", []) if isinstance(data, dict) else []:
            for product in store.get("products", []):
                results.append(
                    {
                        "sku": str(product.get("product_id")),
                        "title": str(product.get("name", "")),
                        "price": product.get("price", 0),
                        "in_stock": product.get("in_stock", True),
                        "store_id": str(store.get("store_id", "")),
                        "store_name": str(store.get("store_name", "")),
                        "eta": str(store.get("eta", "")),
                        "shipping_cost": store.get("shipping_cost", 0),
                    }
                )
        return results


    def get_payment_methods(self, store_type: str) -> list[dict[str, Any]]:
        """Saved payment methods. NOT in checkout/detail — separate resolver."""
        return self._request(
            "GET",
            PAYMENT_RESOLVER_PATH,
            params={
                "origin": "CHECKOUT",
                "store_type": store_type,
                "only-added-pm": "true",
            },
        )

    def set_payment_method(self, store_type: str, payload: dict[str, Any]) -> Any:
        """Bind a payment method to the cart. Without this, Rappi charges
        the order in CASH — silently (discovered by the CLI fork)."""
        return self._request(
            "PUT", PAYMENT_PUT_PATH.format(store_type=store_type), body=payload
        )


def build_payment_payload(method: dict[str, Any]) -> dict[str, Any]:
    """Mirror the Rappi web payload: card reference plus the resolver's
    charge_data, with empty values dropped (they 400 the PUT)."""
    metadata = method.get("metadata") or {}
    charge: dict[str, Any] = dict(metadata.get("charge_data") or {})

    def keep(entries: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in entries.items() if v not in (None, "", [])}

    card_reference = charge.get("account_payment_id")
    return keep(
        {
            **({"card": {"card_reference": card_reference}} if card_reference else {}),
            "payment_method_type": method.get("id"),
            "rappi_credit": {"use_rappi_credit": False},
            "rappi_pay": {"use_rappi_pay": False, "rappi_pay_method_active": False},
            "charge_data": keep(
                {
                    "account_payment_id": charge.get("account_payment_id"),
                    "card_class": charge.get("card_class"),
                    "card_type": charge.get("card_type"),
                    "first_six_digits": charge.get("first_six_digits"),
                    "last_four_digits": charge.get("last_four_digits"),
                    "payment_method_token": charge.get("payment_method_token"),
                    "store_ids": charge.get("store_ids"),
                    "store_type": charge.get("store_type"),
                    "threeds_reference_id": charge.get("threeds_reference_id"),
                    "selected_installments": charge.get("selected_installments", "1"),
                    "user_id": charge.get("account_payment_id"),
                    "language": charge.get("language"),
                    "tags": charge.get("tags"),
                    "online_payment": charge.get("online_payment"),
                    "payment_method": charge.get("payment_method"),
                    "payment_method_description": charge.get(
                        "payment_method_description"
                    ),
                    "payment_method_icon": charge.get("payment_method_icon"),
                    "user_default_refund_payment_method": charge.get(
                        "user_default_refund_payment_method"
                    ),
                    "origin_platform": "web",
                }
            ),
        }
    )


def method_is_cash(method: dict[str, Any]) -> bool:
    charge = (method.get("metadata") or {}).get("charge_data") or {}
    return method.get("id") == "cash" or charge.get("payment_method") == "cash"


def method_needs_3ds(method: dict[str, Any]) -> bool:
    """Fraud-flagged cards cannot be charged from automation: Rappi requires
    a 3D Secure challenge from the app/browser. Detected via resolver tags."""
    tags = " ".join(
        str(tag)
        for tag in [
            method.get("tags"),
            *(method.get("payment_method_tags") or []),
            (method.get("metadata") or {}).get("charge_data", {}).get("tags"),
        ]
        if tag
    ).lower()
    return "require_3ds" in tags or "3ds_by_fraud" in tags
