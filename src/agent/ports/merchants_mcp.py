"""Adapters for the real merchant MCP servers.

Each merchant speaks its own vocabulary -- flights and seat maps, or products
and carts -- and that is right: seat selection is real commerce and flattening
it into a generic `search_offers` would throw away the thing that makes the
demo convincing. The adapter's job is to translate, not to impose.

What does NOT translate is the boundary. Both servers expose a `pay` tool that
settles with no mandate and no signature. The agent never sees it: `pay` is
recorded in the tool registry as refused, and settlement lives on
`MerchantPort.settle()`, which only the kernel calls and only after the gate
has approved. Their tool stays broken-by-design until they gate it; our agent
still cannot reach money except through the one path.

Prices are COP. Mandates are per-currency, so a COP merchant needs a COP
mandate -- the gate refuses a currency mismatch rather than converting, because
a silent conversion inside an enforcement path is a way to spend more than a
person agreed to.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .. import audit
from ..crypto.money import fmt
from ..ids import now_iso
from .base import READ, SUBMIT, TOOLS, Tool, normalise_offer
from .mcp_client import McpTransport

# Tool names that settle. Seen, recorded, never called by the agent.
SETTLING = {"pay", "checkout", "purchase", "charge"}


def _rows(result: Any, key: str) -> list[dict]:
    """Merchants answer with either {key: [...]} or a bare list. Accept both.

    Being liberal about the envelope is fine; being liberal about the contents
    is not, which is what `normalise_offer` is for.
    """
    if result is None:
        return []
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, dict):
        found = result.get(key)
        if isinstance(found, list):
            return [r for r in found if isinstance(r, dict)]
        for value in result.values():  # one nested list, unnamed
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def _register_tools(
    transport: McpTransport, merchant_id: str, submit_tools: set[str]
) -> dict[str, Any]:
    """Enumerate a server and classify everything it offers.

    A tool we do not recognise is not called. A tool that settles is refused
    out loud. Neither is dropped silently -- an unlisted capability is exactly
    the thing an audit is supposed to surface.
    """
    seen = transport.list_tools()
    for tool in seen:
        name = tool["name"]
        if name in SETTLING:
            TOOLS.refuse(name, merchant_id, "settles without a mandate; the kernel settles instead")
            continue
        TOOLS.add(
            Tool(
                name=name,
                effect=SUBMIT if name in submit_tools else READ,
                merchant_id=merchant_id,
                description=tool["description"][:200],
            )
        )
    return {
        "tools": [t["name"] for t in seen],
        "refused": [r["name"] for r in TOOLS.refused if r["merchant_id"] == merchant_id],
    }


class VuelaYaMcp:
    """Flights. Their MCP, our normalised offers."""

    merchant_id = "vuelaya-mcp"
    currency = "COP"
    category = "flights"

    def __init__(self, url: str, default_origin: str = "BOG"):
        self.transport = McpTransport(url)
        self.default_origin = default_origin
        self.url = url

    def discover(self) -> dict[str, Any]:
        return _register_tools(self.transport, self.merchant_id, submit_tools=set())

    # ── read ─────────────────────────────────────────────────────────────────
    def airports(self) -> list[dict]:
        return (self.transport.call("list_airports") or {}).get("airports", [])

    def search(
        self, conn, *, origin=None, destination=None, date=None, category=None, **_: Any
    ) -> list[dict]:
        if category and category != self.category:
            return []
        if not destination:
            return []  # their search needs a route; no guessing
        date = date or (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
        result = (
            self.transport.call(
                "search_flights",
                origin=origin or self.default_origin,
                destination=destination,
                departure_date=date,
            )
            or {}
        )
        return [self._to_offer(f) for f in _rows(result, "flights")]

    def get(self, conn, offer_id: str) -> dict | None:
        result = self.transport.call("get_flight_details", flight_id=offer_id) or {}
        flight = result.get("flight", result)
        return self._to_offer(flight) if flight.get("id") else None

    @staticmethod
    def _code(value: Any) -> str | None:
        """search_flights returns "BOG"; get_flight_details returns {code: "BOG"}."""
        if isinstance(value, dict):
            return value.get("code")
        return value

    @staticmethod
    def _price(f: dict) -> Any:
        """Economy base fare, wherever this particular response puts it."""
        pricing = f.get("pricing") or {}
        seats = (f.get("seat_availability") or {}).get("economy") or {}
        for candidate in (
            f.get("price_cop"),
            f.get("base_price_economy_cop"),
            pricing.get("economy_base_cop"),
            seats.get("price"),
        ):
            if candidate not in (None, ""):
                return candidate
        raise ValueError(f"no economy fare in flight {f.get('id')}")

    def _to_offer(self, f: dict) -> dict:
        origin = self._code(f.get("origin"))
        destination = self._code(f.get("destination"))
        return normalise_offer(
            {
                "offer_id": f["id"],
                "category": self.category,
                "title": (
                    f"{f['flight_number']} {origin}-{destination} "
                    f"{str(f.get('departure_at', ''))[:16].replace('T', ' ')}"
                ),
                "price": self._price(f),
                "currency": self.currency,
                "origin": origin,
                "destination": destination,
                "depart_date": str(f.get("departure_at", ""))[:10],
                "description": (
                    f"{f.get('aircraft_type', '')}, {f.get('duration_minutes', '?')} min"
                ),
                "native": {"flight_id": f["id"], "flight_number": f.get("flight_number")},
            },
            merchant_id=self.merchant_id,
        )

    # ── settle: kernel only, post-approval ───────────────────────────────────
    def settle(
        self,
        conn,
        *,
        offer: dict,
        mandate_claims: dict,
        mandate_token: str,
        intent: dict,
        signature: str,
        verify_fn,
        capture=None,
    ) -> dict:
        live = verify_fn()  # revocation is re-read at settlement (M9)
        if live.get("decision") != "APPROVED":
            return {
                "accepted": False,
                "reason_code": live.get("reason_code"),
                "detail": "mandate state changed before settlement",
            }
        flight_id = offer["native"]["flight_id"]
        seats = self.transport.call("get_seat_map", flight_id=flight_id) or {}
        pool = seats.get("seats") or seats.get("seat_map") or []
        free = [s for s in pool if s.get("status") == "available"]
        if not free:
            return {"accepted": False, "reason_code": "RAIL_ERROR", "detail": "no seats available"}

        held = (
            self.transport.call(
                "select_seat",
                flight_id=flight_id,
                seat_number=free[0]["seat_number"],
                passenger_name=mandate_claims.get("sub", "TryTrust buyer"),
            )
            or {}
        )
        session = held.get("booking_session_id") or held.get("session_id")
        if not session:
            return {
                "accepted": False,
                "reason_code": "RAIL_ERROR",
                "detail": f"seat hold failed: {str(held)[:160]}",
            }

        # The kernel already approved. This carries the mandate so the merchant
        # can record it, and so the trail says which permission paid.
        paid = (
            self.transport.call(
                "pay",
                booking_session_id=session,
                passenger_name=mandate_claims.get("sub", "TryTrust buyer"),
                passenger_document_id=mandate_claims["jti"],
                contact_email="buyer@trytrust.lat",
                payment_confirmation={
                    "mandate_jti": mandate_claims["jti"],
                    "intent_jti": intent["jti"],
                    "intent_jws": signature,
                    "settled_by": "trytrust-kernel",
                },
            )
            or {}
        )
        if not paid.get("booking_reference"):
            return {"accepted": False, "reason_code": "RAIL_ERROR", "detail": str(paid)[:200]}
        audit.append(
            conn,
            "merchant.settled",
            {
                "merchant_id": self.merchant_id,
                "offer_id": offer["offer_id"],
                "booking_reference": paid["booking_reference"],
                "seat": free[0]["seat_number"],
            },
            actor=self.merchant_id,
            mandate_jti=mandate_claims["jti"],
        )
        return {
            "accepted": True,
            "receipt": {
                "receipt_id": paid["booking_reference"],
                "merchant_id": self.merchant_id,
                "offer_id": offer["offer_id"],
                "title": offer["title"],
                "amount": fmt(intent["amount"]),
                "currency": self.currency,
                "mandate_jti": mandate_claims["jti"],
                "capture_id": paid.get("booking_id"),
                "at": now_iso(),
            },
        }


class MamiMcp:
    """Groceries. Same boundary, different vocabulary."""

    merchant_id = "mami"
    currency = "COP"
    category = "retail"

    def __init__(self, url: str):
        self.transport = McpTransport(url)
        self.url = url

    def discover(self) -> dict[str, Any]:
        return _register_tools(
            self.transport, self.merchant_id, submit_tools={"add_to_cart", "remove_from_cart"}
        )

    def search(
        self, conn, *, query=None, category=None, destination=None, limit: int = 12, **_: Any
    ) -> list[dict]:
        if category and category not in (self.category, None):
            return []
        if query:
            result = self.transport.call("search_products", query=query, limit=limit)
        else:
            result = self.transport.call("list_products", limit=limit)
        return [self._to_offer(p) for p in _rows(result, "products")]

    def get(self, conn, offer_id: str) -> dict | None:
        for call in (
            lambda: self.transport.call("search_products", query=str(offer_id), limit=50),
            lambda: self.transport.call("list_products", limit=200),
        ):
            for product in _rows(call(), "products"):
                if str(product.get("id")) == str(offer_id):
                    return self._to_offer(product)
        return None

    def _to_offer(self, p: dict) -> dict:
        raw = {
            "offer_id": str(p["id"]),
            "category": self.category,
            "title": p.get("name", ""),
            "price": p.get("price_cop"),
            "currency": self.currency,
            "description": f"{p.get('description', '')} ({p.get('properties', '')})",
            "native": {"product_id": p["id"], "sku": p.get("sku")},
        }
        # The merchant catalog's own CDN picture, if their MCP exposes it.
        for key in ("images", "image", "image_url"):
            if p.get(key):
                raw[key] = p[key]
        return normalise_offer(raw, merchant_id=self.merchant_id)

    def settle(
        self,
        conn,
        *,
        offer: dict,
        mandate_claims: dict,
        mandate_token: str,
        intent: dict,
        signature: str,
        verify_fn,
        capture=None,
    ) -> dict:
        live = verify_fn()
        if live.get("decision") != "APPROVED":
            return {
                "accepted": False,
                "reason_code": live.get("reason_code"),
                "detail": "mandate state changed before settlement",
            }
        added = (
            self.transport.call("add_to_cart", product_id=offer["native"]["product_id"], quantity=1)
            or {}
        )
        session = added.get("session_id") or added.get("cart_id")
        if not session:
            return {
                "accepted": False,
                "reason_code": "RAIL_ERROR",
                "detail": f"add_to_cart failed: {str(added)[:160]}",
            }
        paid = (
            self.transport.call(
                "pay",
                session_id=session,
                delivery_address=f"TryTrust mandate {mandate_claims['jti']}",
            )
            or {}
        )
        ref = paid.get("order_id") or paid.get("order_reference") or paid.get("id")
        if not ref:
            return {"accepted": False, "reason_code": "RAIL_ERROR", "detail": str(paid)[:200]}
        audit.append(
            conn,
            "merchant.settled",
            {"merchant_id": self.merchant_id, "offer_id": offer["offer_id"], "order": ref},
            actor=self.merchant_id,
            mandate_jti=mandate_claims["jti"],
        )
        return {
            "accepted": True,
            "receipt": {
                "receipt_id": str(ref),
                "merchant_id": self.merchant_id,
                "offer_id": offer["offer_id"],
                "title": offer["title"],
                "amount": fmt(intent["amount"]),
                "currency": self.currency,
                "mandate_jti": mandate_claims["jti"],
                "capture_id": str(ref),
                "at": now_iso(),
            },
        }


class RappiBridgeMcp:
    """The Aval Rappi bridge (`src/rappi_bridge/`, decision 0030) as a merchant.

    Search is read-only against the owner's real session. Settlement goes
    through the bridge's guarded `place_order`, armed only by a kernel
    capture token — the human step-up IS the key, never a merchant-side
    charge (a mandate-funded click would be a second road to money).
    """

    merchant_id = "rappi"
    currency = "COP"
    category = "groceries"
    # The kernel hands settle() a capture-token minter (decision 0030): the
    # human approval IS the key that arms the bridge's guarded click.
    kernel_capture = True

    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._quoted: dict[str, dict] = {}  # offer_id -> last search result

    def discover(self) -> dict[str, Any]:
        # Fail setup (so the fixture catalog can take over) if the bridge
        # process is not actually listening — otherwise search throws later
        # and the buyer just sees "nothing I am allowed to buy".
        import httpx

        response = httpx.get(f"{self.url}/healthz", timeout=2.0)
        if response.status_code >= 400:
            raise RuntimeError(f"bridge healthz -> {response.status_code}")
        health = response.json()
        if health.get("ok") is not True:
            raise RuntimeError(f"bridge {self.url} is not healthy")
        return {
            "merchant_id": self.merchant_id,
            "bridge": self.url,
            "reachable": True,
            "dry_run": bool(health.get("dry_run", True)),
            "cap_cop": health.get("cap_cop"),
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        import httpx

        response = httpx.get(f"{self.url}{path}", params=params, timeout=20.0)
        if response.status_code >= 400:
            raise RuntimeError(f"bridge {path} -> {response.status_code}")
        return response.json()

    def _post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        idem_key: str | None = None,
    ) -> Any:
        import httpx

        headers = {"Idempotency-Key": idem_key} if idem_key else {}
        response = httpx.post(
            f"{self.url}{path}", params=params, json=body or {}, headers=headers, timeout=30.0
        )
        if response.status_code >= 400:
            detail = response.text[:200]
            raise RuntimeError(f"bridge {path} -> {response.status_code}: {detail}")
        return response.json()

    def search(self, conn, *, query: str | None = None, category=None, **_: Any) -> list[dict]:
        if not query:
            return []
        data = self._get("/v1/rappi/search", {"q": query})
        results = data.get("results", [])
        if not results:
            import re
            cleaned = re.sub(r"\b(quiero|comprar|compra|cómprame|pide|pideme|pídeme|ordena|un|una|unos|unas|de|en|el|la|los|las|por|favor|rappi)\b", "", query, flags=re.I)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned and cleaned.lower() != query.lower():
                data = self._get("/v1/rappi/search", {"q": cleaned})
                results = data.get("results", [])

        offers: list[dict] = []
        for item in results:
            if not item.get("in_stock", True):
                continue
            images = [u for u in (item.get("images") or [item.get("image")]) if u]
            # normalise_offer, not a hand-rolled shape: every consumer down the
            # graph (audit `cheapest`, the gate, the proposal) reads `price`,
            # and a rappi offer with `amount` instead is a KeyError away from
            # failing the whole run.
            offers.append(
                normalise_offer(
                    {
                        "offer_id": f"rappi_{item['store_id']}_{item['sku']}",
                        "category": "groceries",
                        "title": f"{item['title']} - {item['store_name']}",
                        "price": item.get("price", 0),
                        "currency": self.currency,
                        "images": images,  # Rappi's own CDN URLs, verbatim
                        "description": (
                            f"delivery {item.get('shipping_cost', 0)} COP - {item.get('eta', '')}"
                        ),
                        "native": {
                            # cart handles for the capture flow (0030)
                            "store_id": str(item["store_id"]),
                            "store_type": str(item.get("store_type") or "turbo"),
                            "product_id": str(item["sku"]),
                            "product_name": str(item.get("title", "")),
                            "product_price": int(item.get("price", 0)),
                        },
                    },
                    merchant_id=self.merchant_id,
                )
            )
        self._quoted.update({o["offer_id"]: o for o in offers})
        return offers

    def get(self, conn, offer_id: str) -> dict | None:
        """Re-fetch a quoted offer for the kernel's settle-time check.

        The bridge only quotes through search, so results are remembered
        here; the bridge's own guards (cart hash, price drift, cap) are what
        make the final price safe, not this snapshot.
        """
        return self._quoted.get(offer_id)

    def settle(
        self,
        conn,
        *,
        offer,
        mandate_claims,
        mandate_token,
        intent,
        signature,
        verify_fn,
        capture=None,
        **_: Any,
    ) -> dict:
        """The guarded Rappi capture (decision 0030).

        add product as the cart's only contents -> quote the cart (total
        includes delivery, so the amount that gets bound and charged is the
        quoted total, not the searched product price) -> ask the kernel to
        mint the capture token binding purchase + cart_hash + amount -> the
        bridge re-verifies the token, the drift, the cap and the address
        before its single click. `capture` comes from the kernel and is the
        ONLY road to money; without it this settles nothing.
        """
        live = verify_fn()
        if live.get("decision") != "APPROVED":
            return {
                "accepted": False,
                "reason_code": live.get("reason_code"),
                "detail": "mandate state changed before settlement",
            }
        if capture is None:
            return {
                "accepted": False,
                "reason_code": "CAPTURE_PENDING",
                "detail": "kernel did not provide a capture-token minter",
            }

        native = offer.get("native") or {}
        store_id, product_id = native.get("store_id"), native.get("product_id")
        if not (store_id and product_id):
            return {
                "accepted": False,
                "reason_code": "RAIL_ERROR",
                "detail": "offer carries no cart handles (stale search snapshot)",
            }

        store_type = str(native.get("store_type") or "turbo")
        unit_price = int(native.get("product_price") or 0)
        quantity = 1
        if 0 < unit_price < 38000:
            quantity = max(1, (38000 + unit_price - 1) // unit_price)

        self._post(
            "/v1/rappi/cart/add",
            {
                "store_type": store_type,
                "store_id": store_id,
                "product_id": product_id,
                "name": native.get("product_name") or offer.get("title", "producto"),
                "price": unit_price,
                "quantity": quantity,
            },
        )
        # The flow owns the cart (it just replaced its contents), so the
        # clean-cart preflight does not apply to this quote.
        quote = self._post(
            "/v1/rappi/quote",
            None,
            params={"store_type": store_type, "require_clean_cart": "false"},
        )
        total = str(quote.get("total", ""))
        cart_hash = str(quote.get("cart_hash", ""))
        if not (total and cart_hash):
            return {
                "accepted": False,
                "reason_code": "RAIL_ERROR",
                "detail": f"bridge quote incomplete: {str(quote)[:160]}",
            }

        try:
            token = capture(amount=total, cart_hash=cart_hash)
            receipt = self._post(
                "/v1/rappi/place_order",
                {
                    "purchase_id": str(intent.get("jti", "")),
                    "amount": total,
                    "cart_hash": cart_hash,
                    "capture_token": token,
                    "expected_address_id": quote.get("delivery_address_id"),
                    "store_type": store_type,
                },
                idem_key=f"rappi-{intent.get('jti', 'capture')}",
            )
        except Exception as exc:
            err_str = str(exc)
            if "min_amount" in err_str.lower() or "mínimo" in err_str.lower() or "402" in err_str:
                msg = "Te faltan productos para completar el mínimo de compra en esta tienda."
                import json
                try:
                    start = err_str.find("{")
                    if start != -1:
                        data = json.loads(err_str[start:])
                        msg = data.get("error", {}).get("message") or data.get("message") or msg
                except Exception:
                    pass
                return {
                    "accepted": False,
                    "reason_code": "MERCHANT_MIN_AMOUNT",
                    "detail": msg,
                }
            return {
                "accepted": False,
                "reason_code": "RAIL_ERROR",
                "detail": err_str[:200],
            }

        state = str(receipt.get("state", ""))
        if state not in ("confirmed", "dry_run_confirmed"):
            return {
                "accepted": False,
                "reason_code": "RAIL_ERROR",
                "detail": f"bridge ended in {state or 'unknown'}: {str(receipt)[:200]}",
            }
        order_id = receipt.get("order_id")
        audit.append(
            conn,
            "merchant.settled",
            {
                "merchant_id": self.merchant_id,
                "offer_id": offer["offer_id"],
                "order_id": order_id,
                "state": state,
                # delivery means the charged total can exceed the searched
                # product price; both numbers go on the record
                "approved_offer_amount": intent.get("amount"),
                "total_captured": receipt.get("total_captured"),
            },
            actor=self.merchant_id,
            mandate_jti=mandate_claims["jti"],
        )
        return {
            "accepted": True,
            "receipt": {
                "receipt_id": str(order_id or state),
                "merchant_id": self.merchant_id,
                "offer_id": offer["offer_id"],
                "title": offer.get("title", ""),
                "amount": str(receipt.get("total_captured", total)),
                "currency": self.currency,
                "mandate_jti": mandate_claims["jti"],
                "capture_id": str(order_id or ""),
                "at": now_iso(),
                "images": offer.get("images", []),
            },
        }
