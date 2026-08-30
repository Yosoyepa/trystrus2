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
