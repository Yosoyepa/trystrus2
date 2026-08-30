"""The in-process merchant, behind the same port as the remote ones.

Keeping the mock on the MerchantPort interface is what stops the abstraction
from being decorative: the local merchant and a real MCP server are reached
through identical code, so the tests that pass against one mean something about
the other.
"""

from __future__ import annotations

from typing import Any

from ..mocks import merchant as _mock
from .base import TOOLS, Tool, normalise_offer


class LocalMerchant:
    merchant_id = _mock.MERCHANT_ID  # "vuelaya"
    currency = "USD"

    def discover(self) -> dict[str, Any]:
        for name, effect in (
            ("search_offers", "read"),
            ("get_offer", "read"),
            ("request_purchase", "submit"),
        ):
            TOOLS.add(Tool(name=name, effect=effect, merchant_id=self.merchant_id))
        return {"tools": TOOLS.callable_names(self.merchant_id), "refused": []}

    def search(
        self,
        conn,
        *,
        origin=None,
        destination=None,
        date=None,
        category=None,
        limit: int = 12,
        **_: Any,
    ) -> list[dict]:
        return [
            self._to_offer(o)
            for o in _mock.search_offers(
                conn,
                origin=origin,
                destination=destination,
                date=date,
                category=category,
                merchant_id=self.merchant_id,
                limit=limit,
            )
        ]

    def get(self, conn, offer_id: str) -> dict | None:
        found = _mock.get_offer(conn, offer_id)
        return self._to_offer(found) if found else None

    def _to_offer(self, o: dict) -> dict:
        return normalise_offer({**o, "native": {}}, merchant_id=self.merchant_id)

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
        """Goes through the merchant's own checkout, which verifies the mandate
        signature against the published JWKS before charging (C8)."""
        from ..mocks.rail import RailError

        try:
            return _mock.checkout_charge(
                conn,
                mandate_token=mandate_token,
                intent=intent,
                intent_sig=signature,
                verify_fn=verify_fn,
            )
        except RailError as exc:
            return {"accepted": False, "reason_code": exc.code, "detail": str(exc)}


_GROCERY_FAMILY = {"food", "groceries", "retail"}


class LocalRappi(LocalMerchant):
    """In-process grocery catalog under merchant_id `rappi`.

    Used when the live Rappi bridge is down so a water/pizza request still
    finds something the mandate allows, instead of a silent empty search.
    Settlement is the same mock checkout as VuelaYa — still through the gate.
    """

    merchant_id = "rappi"
    currency = "COP"

    def _ensure_catalog(self, conn) -> None:
        row = conn.execute(
            "SELECT 1 FROM offers WHERE merchant_id=? LIMIT 1", (self.merchant_id,)
        ).fetchone()
        if row is None:
            _mock.seed(conn)

    def search(
        self,
        conn,
        *,
        origin=None,
        destination=None,
        date=None,
        category=None,
        query: str | None = None,
        limit: int = 12,
        **_: Any,
    ) -> list[dict]:
        if category and category not in _GROCERY_FAMILY:
            return []
        self._ensure_catalog(conn)
        rows = _mock.search_offers(conn, merchant_id=self.merchant_id, limit=50)
        offers = [self._to_offer(o) for o in rows]
        if query:
            tokens = [t for t in query.lower().split() if len(t) > 3]
            hits = [o for o in offers if any(t in (o.get("title") or "").lower() for t in tokens)]
            if hits:
                return hits[:limit]
        return offers[:limit]

    def get(self, conn, offer_id: str) -> dict | None:
        self._ensure_catalog(conn)
        return super().get(conn, offer_id)
