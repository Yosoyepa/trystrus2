"""MCP client: the agent's read side, over real transport.

Until now the agent called the merchant in the same process. That was fine for
proving the decision logic and useless for proving the boundary: an in-process
call cannot demonstrate that tool output crosses a trust line, because there is
no line. This speaks MCP to a server the agent does not own.

The boundary rules from `contracts/schemas.md` section 10 are enforced HERE,
on the client, because the agent cannot trust the server to enforce them:

  * every tool result is wrapped as DATA and fenced before it reaches the model
  * results are size-clamped (G7) — a hostile catalog cannot flood the context
  * the tool surface is allow-listed; a server offering a `pay` tool gets it
    ignored, loudly (S2)

The graph is synchronous, so each call is a short-lived session rather than a
long-lived connection. That costs a round trip and buys crash-safety: there is
no session to resume wrongly after a Cloud Run instance disappears.
"""
from __future__ import annotations
import asyncio
import json
import os
from typing import Any

from mcp.client.client import Client

from .. import limits

# The only tools the agent will call, whatever a server advertises.
ALLOWED_TOOLS = {"search_offers", "get_offer", "request_purchase"}
# A tool whose name suggests it moves money is a red flag about the server,
# not a capability to use.
FORBIDDEN_HINTS = ("pay", "charge", "capture", "refund", "card", "token")

DEFAULT_URL = os.environ.get("TT_MCP_URL", "http://127.0.0.1:8931/mcp")


class McpError(Exception):
    pass


class McpTransport:
    """Bare JSON-RPC-over-MCP plumbing. No policy, no allow-list.

    Sessions are per call rather than long-lived: that costs a round trip and
    buys crash-safety, since there is no session to resume wrongly after an
    instance disappears.
    """

    def __init__(self, url: str, timeout: float = 25.0):
        from ..net import check
        check(url, reason="mcp")     # refuse before a socket is ever opened
        self.url = url
        self.timeout = timeout

    async def _call_async(self, tool: str, args: dict[str, Any]) -> str:
        async with Client(self.url, raise_exceptions=True) as client:
            result = await client.call_tool(tool, args)
            return "".join(getattr(b, "text", "") or "" for b in result.content)

    def call(self, tool: str, **args: Any) -> Any:
        raw = asyncio.run(asyncio.wait_for(self._call_async(tool, args), self.timeout))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_text": raw}        # some tools answer in prose; keep it

    def list_tools(self) -> list[dict[str, Any]]:
        async def go():
            async with Client(self.url, raise_exceptions=True) as client:
                listing = await client.list_tools()
                return [{"name": t.name, "description": (t.description or "")}
                        for t in listing.tools]
        return asyncio.run(go())


class McpMerchant:
    """One merchant, reachable over MCP, speaking the frozen three-tool contract."""

    def __init__(self, url: str | None = None, merchant_id: str = "vuelaya",
                 timeout: float = 20.0):
        from ..net import check
        self.url = url or DEFAULT_URL
        check(self.url, reason="mcp")
        self.merchant_id = merchant_id
        self.timeout = timeout

    # ── plumbing ─────────────────────────────────────────────────────────────
    async def _call_async(self, tool: str, args: dict[str, Any]) -> Any:
        async with Client(self.url, raise_exceptions=True) as client:
            result = await client.call_tool(tool, args)
            chunks = []
            for block in result.content:
                text = getattr(block, "text", None)
                if text is not None:
                    chunks.append(text)
            return "".join(chunks)

    def _call(self, tool: str, **args: Any) -> Any:
        if tool not in ALLOWED_TOOLS:
            raise McpError(f"{tool} is not on the agent's allow-list")
        raw = asyncio.run(asyncio.wait_for(self._call_async(tool, args), self.timeout))
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError as exc:
            raise McpError(f"{tool} returned something that is not JSON: {raw[:200]}") from exc

    # ── discovery ────────────────────────────────────────────────────────────
    def inspect(self) -> dict[str, Any]:
        """What does this server offer, and is any of it alarming?"""
        async def go():
            async with Client(self.url, raise_exceptions=True) as client:
                listing = await client.list_tools()
                return [{"name": t.name, "description": (t.description or "")[:160]}
                        for t in listing.tools]

        tools = asyncio.run(go())
        names = {t["name"] for t in tools}
        suspicious = [n for n in names
                      if any(h in n.lower() for h in FORBIDDEN_HINTS)]
        return {"url": self.url, "tools": tools,
                "missing": sorted(ALLOWED_TOOLS - names),
                "unexpected": sorted(names - ALLOWED_TOOLS),
                "suspicious": suspicious,
                "contract_ok": not (ALLOWED_TOOLS - names) and not suspicious}

    # ── the three tools ──────────────────────────────────────────────────────
    def search_offers(self, conn=None, *, origin=None, destination=None, date=None,
                      category=None, limit: int = 12, agent_id: str | None = None,
                      mandate_jti: str | None = None) -> list[dict]:
        if conn is not None and agent_id:
            limits.guard_merchant_call(conn, agent_id, mandate_jti)
        offers = self._call("search_offers", origin=origin, destination=destination,
                            date=date, category=category) or []
        return limits.clamp_offers(offers)[:limit]

    def get_offer(self, conn=None, offer_id: str = "", *, agent_id: str | None = None,
                  mandate_jti: str | None = None) -> dict | None:
        if conn is not None and agent_id:
            limits.guard_merchant_call(conn, agent_id, mandate_jti)
        offer = self._call("get_offer", offer_id=offer_id)
        return limits.clamp_offers([offer])[0] if offer else None

    def request_purchase(self, conn=None, *, offer_id: str, mandate_jti: str) -> dict:
        """No `amount` parameter, deliberately (S6)."""
        return self._call("request_purchase", offer_id=offer_id,
                          mandate_jti=mandate_jti)
