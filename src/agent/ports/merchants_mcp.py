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
from .base import READ, SUBMIT, Tool, TOOLS, normalise_offer
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
        for value in result.values():        # one nested list, unnamed
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def _register_tools(transport: McpTransport, merchant_id: str,
                    submit_tools: set[str]) -> dict[str, Any]:
    """Enumerate a server and classify everything it offers.

    A tool we do not recognise is not called. A tool that settles is refused
    out loud. Neither is dropped silently -- an unlisted capability is exactly
    the thing an audit is supposed to surface.
    """
    seen = transport.list_tools()
    for tool in seen:
        name = tool["name"]
        if name in SETTLING:
            TOOLS.refuse(name, merchant_id,
                         "settles without a mandate; the kernel settles instead")
            continue
        TOOLS.add(Tool(name=name,
                       effect=SUBMIT if name in submit_tools else READ,
                       merchant_id=merchant_id,
                       description=tool["description"][:200]))
    return {"tools": [t["name"] for t in seen],
            "refused": [r["name"] for r in TOOLS.refused if r["merchant_id"] == merchant_id]}


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

    def search(self, conn, *, origin=None, destination=None, date=None,
               category=None, **_: Any) -> list[dict]:
        if category and category != self.category:
            return []
        if not destination:
            return []                      # their search needs a route; no guessing
        date = date or (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
        result = self.transport.call(
            "search_flights", origin=origin or self.default_origin,
            destination=destination, departure_date=date) or {}
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
        for candidate in (f.get("price_cop"), f.get("base_price_economy_cop"),
                          pricing.get("economy_base_cop"), seats.get("price")):
            if candidate not in (None, ""):
                return candidate
        raise ValueError(f"no economy fare in flight {f.get('id')}")

    def _to_offer(self, f: dict) -> dict:
        origin = self._code(f.get("origin"))
        destination = self._code(f.get("destination"))
        return normalise_offer({
            "offer_id": f["id"],
            "category": self.category,
            "title": (f"{f['flight_number']} {origin}-{destination} "
                      f"{str(f.get('departure_at', ''))[:16].replace('T', ' ')}"),
            "price": self._price(f),
            "currency": self.currency,
            "origin": origin,
            "destination": destination,
            "depart_date": str(f.get("departure_at", ""))[:10],
            "description": (f"{f.get('aircraft_type', '')}, "
                            f"{f.get('duration_minutes', '?')} min"),
            "native": {"flight_id": f["id"], "flight_number": f.get("flight_number")},
        }, merchant_id=self.merchant_id)

    # ── settle: kernel only, post-approval ───────────────────────────────────
    def settle(self, conn, *, offer: dict, mandate_claims: dict, mandate_token: str,
               intent: dict, signature: str, verify_fn) -> dict:
        live = verify_fn()          # revocation is re-read at settlement (M9)
        if live.get("decision") != "APPROVED":
            return {"accepted": False, "reason_code": live.get("reason_code"),
                    "detail": "mandate state changed before settlement"}
        flight_id = offer["native"]["flight_id"]
        seats = self.transport.call("get_seat_map", flight_id=flight_id) or {}
        pool = seats.get("seats") or seats.get("seat_map") or []
        free = [s for s in pool if s.get("status") == "available"]
        if not free:
            return {"accepted": False, "reason_code": "RAIL_ERROR",
                    "detail": "no seats available"}

        held = self.transport.call(
            "select_seat", flight_id=flight_id, seat_number=free[0]["seat_number"],
            passenger_name=mandate_claims.get("sub", "TryTrust buyer")) or {}
        session = held.get("booking_session_id") or held.get("session_id")
        if not session:
            return {"accepted": False, "reason_code": "RAIL_ERROR",
                    "detail": f"seat hold failed: {str(held)[:160]}"}

        # The kernel already approved. This carries the mandate so the merchant
        # can record it, and so the trail says which permission paid.
        paid = self.transport.call(
            "pay", booking_session_id=session,
            passenger_name=mandate_claims.get("sub", "TryTrust buyer"),
            passenger_document_id=mandate_claims["jti"],
            contact_email="buyer@trytrust.lat",
            payment_confirmation={"mandate_jti": mandate_claims["jti"],
                                  "intent_jti": intent["jti"],
                                  "intent_jws": signature,
                                  "settled_by": "trytrust-kernel"}) or {}
        if not paid.get("booking_reference"):
            return {"accepted": False, "reason_code": "RAIL_ERROR",
                    "detail": str(paid)[:200]}
        audit.append(conn, "merchant.settled",
                     {"merchant_id": self.merchant_id, "offer_id": offer["offer_id"],
                      "booking_reference": paid["booking_reference"],
                      "seat": free[0]["seat_number"]},
                     actor=self.merchant_id, mandate_jti=mandate_claims["jti"])
        return {"accepted": True, "receipt": {
            "receipt_id": paid["booking_reference"], "merchant_id": self.merchant_id,
            "offer_id": offer["offer_id"], "title": offer["title"],
            "amount": fmt(intent["amount"]), "currency": self.currency,
            "mandate_jti": mandate_claims["jti"], "capture_id": paid.get("booking_id"),
            "at": now_iso()}}


class MamiMcp:
    """Groceries. Same boundary, different vocabulary."""

    merchant_id = "mami"
    currency = "COP"
    category = "retail"

    def __init__(self, url: str):
        self.transport = McpTransport(url)
        self.url = url

    def discover(self) -> dict[str, Any]:
        return _register_tools(self.transport, self.merchant_id,
                               submit_tools={"add_to_cart", "remove_from_cart"})

    def search(self, conn, *, query=None, category=None, destination=None,
               limit: int = 12, **_: Any) -> list[dict]:
        if category and category not in (self.category, None):
            return []
        if query:
            result = self.transport.call("search_products", query=query, limit=limit)
        else:
            result = self.transport.call("list_products", limit=limit)
        return [self._to_offer(p) for p in _rows(result, "products")]

    def get(self, conn, offer_id: str) -> dict | None:
        for call in (lambda: self.transport.call("search_products",
                                                 query=str(offer_id), limit=50),
                     lambda: self.transport.call("list_products", limit=200)):
            for product in _rows(call(), "products"):
                if str(product.get("id")) == str(offer_id):
                    return self._to_offer(product)
        return None

    def _to_offer(self, p: dict) -> dict:
        return normalise_offer({
            "offer_id": str(p["id"]),
            "category": self.category,
            "title": p.get("name", ""),
            "price": p.get("price_cop"),
            "currency": self.currency,
            "description": f"{p.get('description', '')} ({p.get('properties', '')})",
            "native": {"product_id": p["id"], "sku": p.get("sku")},
        }, merchant_id=self.merchant_id)

    def settle(self, conn, *, offer: dict, mandate_claims: dict, mandate_token: str,
               intent: dict, signature: str, verify_fn) -> dict:
        live = verify_fn()
        if live.get("decision") != "APPROVED":
            return {"accepted": False, "reason_code": live.get("reason_code"),
                    "detail": "mandate state changed before settlement"}
        added = self.transport.call(
            "add_to_cart", product_id=offer["native"]["product_id"], quantity=1) or {}
        session = added.get("session_id") or added.get("cart_id")
        if not session:
            return {"accepted": False, "reason_code": "RAIL_ERROR",
                    "detail": f"add_to_cart failed: {str(added)[:160]}"}
        paid = self.transport.call(
            "pay", session_id=session,
            delivery_address=f"TryTrust mandate {mandate_claims['jti']}") or {}
        ref = paid.get("order_id") or paid.get("order_reference") or paid.get("id")
        if not ref:
            return {"accepted": False, "reason_code": "RAIL_ERROR",
                    "detail": str(paid)[:200]}
        audit.append(conn, "merchant.settled",
                     {"merchant_id": self.merchant_id, "offer_id": offer["offer_id"],
                      "order": ref},
                     actor=self.merchant_id, mandate_jti=mandate_claims["jti"])
        return {"accepted": True, "receipt": {
            "receipt_id": str(ref), "merchant_id": self.merchant_id,
            "offer_id": offer["offer_id"], "title": offer["title"],
            "amount": fmt(intent["amount"]), "currency": self.currency,
            "mandate_jti": mandate_claims["jti"], "capture_id": str(ref),
            "at": now_iso()}}
