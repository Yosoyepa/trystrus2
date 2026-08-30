"""Extension points: protocols and registries. No plugin machinery.

Adding a merchant, a payment rail, a model provider or a channel is a class and
a `register()` call. Everything the agent talks to goes through one of these, so
a new integration cannot quietly acquire a capability the old one did not have.

The important one is `ToolRegistry`. Every tool an agent may call declares an
`effect`, and the only permitted effects are `read` and `submit`. That turns
S2 -- "no tool reaches money" -- from a claim you verify by reading code into
one assertion you can run:

    assert all(t.effect in ("read", "submit") for t in TOOLS)

A merchant that offers a settling tool (both of ours currently do) gets it
recorded and never called.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..crypto.money import fmt

READ, SUBMIT = "read", "submit"
EFFECTS = (READ, SUBMIT)


def offer_images(raw: dict) -> list[str]:
    """Every merchant CDN picture this offer carries, first one primary.

    Merchants name the field `image`, `image_url` or `images`; whatever
    arrives is passed through untouched -- these are the merchant's own CDN
    URLs, and rewriting them would break the picture the buyer can verify.
    """
    urls: list[str] = []
    candidates = raw.get("images")
    if isinstance(candidates, str):
        candidates = [candidates]
    for url in candidates or []:
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    for key in ("image", "image_url"):
        url = raw.get(key)
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return urls


def normalise_offer(raw: dict, *, merchant_id: str) -> dict:
    """One offer shape, whatever the merchant calls its things."""
    return {
        "offer_id": str(raw["offer_id"]),
        "merchant_id": merchant_id,
        "category": raw.get("category", "other"),
        "title": raw.get("title", ""),
        "price": fmt(raw["price"]),
        "currency": raw.get("currency", "USD"),
        "origin": raw.get("origin"),
        "destination": raw.get("destination"),
        "depart_date": raw.get("depart_date"),
        "description": raw.get("description") or "",
        "images": offer_images(raw),  # merchant CDN URLs, verbatim
        "native": raw.get("native", {}),  # merchant-specific handles, opaque to us
    }


# ── merchants ────────────────────────────────────────────────────────────────
@runtime_checkable
class MerchantPort(Protocol):
    merchant_id: str
    currency: str

    def search(self, conn, **criteria: Any) -> list[dict]:
        """Read-only. Returns normalised offers."""

    def get(self, conn, offer_id: str) -> dict | None:
        """Read-only."""

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
    ) -> dict:
        """Called ONLY by the kernel, ONLY after the gate approved.

        No agent code path reaches this. It lives on the merchant port rather
        than in the tool registry precisely so it cannot appear in a tool
        listing -- a capability the agent cannot name is a capability it cannot
        be talked into using.

        `verify_fn` re-reads mandate state at settlement time, which is what
        keeps revocation synchronous (M9).
        """


MERCHANTS: dict[str, MerchantPort] = {}


def register_merchant(merchant: MerchantPort) -> MerchantPort:
    MERCHANTS[merchant.merchant_id] = merchant
    return merchant


def _bootstrap_local() -> None:
    """Register the in-process merchant if no one has registered anything.

    Without this the library only works after an explicit setup() call, which
    turns every test and every script into a place where the registry can be
    forgotten. The local merchant is always safe to have: it is the mock, and
    the mandate's scope still decides whether anyone may buy from it.
    """
    if MERCHANTS:
        return
    from .local import LocalMerchant

    merchant = LocalMerchant()
    merchant.discover()
    register_merchant(merchant)


def merchant_for(offer_or_id: dict | str | None) -> MerchantPort:
    _bootstrap_local()
    key = offer_or_id["merchant_id"] if isinstance(offer_or_id, dict) else offer_or_id
    if key is None:
        key = next(iter(MERCHANTS))
    if key not in MERCHANTS:
        raise KeyError(f"no merchant registered as {key!r}")
    return MERCHANTS[key]


def search_all(conn, *, allowed: list[str] | None = None, **criteria: Any) -> list[dict]:
    """Fan out across every registered merchant, concurrently.

    `allowed` is the mandate's `scope.merchants`. Filtering here is a courtesy
    that saves pointless calls; the gate enforces it regardless, so registering
    a merchant never widens anyone's permission.

    Merchants are independent network calls, so they run at the same time --
    the slowest merchant sets the latency, not the sum of all of them. Results
    are collected in registry order so the fan-out stays deterministic, and
    one broken merchant still cannot blind the agent.
    """
    _bootstrap_local()
    targets = [
        (merchant_id, merchant)
        for merchant_id, merchant in MERCHANTS.items()
        if not allowed or merchant_id in allowed
    ]
    offers: list[dict] = []
    failures: list[tuple[str, Exception]] = []
    if not targets:
        return offers
    with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as pool:
        futures = [
            (merchant_id, pool.submit(merchant.search, conn, **criteria))
            for merchant_id, merchant in targets
        ]
        for merchant_id, future in futures:
            try:
                offers.extend(future.result())
            except Exception as exc:  # one broken merchant must not blind the agent
                failures.append((merchant_id, exc))
    for merchant_id, exc in failures:
        from .. import audit

        audit.append(
            conn,
            "merchant.unreachable",
            {"merchant_id": merchant_id, "error": str(exc)[:300]},
            relay=False,
        )
    return offers


# ── payment rails ────────────────────────────────────────────────────────────
@runtime_checkable
class RailPort(Protocol):
    rail_id: str

    def vault(self, conn, mandate_jti: str, label: str) -> str: ...
    def capture(
        self, conn, *, token_ref: str, amount: str, currency: str, request_id: str
    ) -> dict: ...
    def delete_token(self, conn, token_ref: str) -> dict: ...


RAILS: dict[str, RailPort] = {}


def register_rail(rail: RailPort) -> RailPort:
    RAILS[rail.rail_id] = rail
    return rail


# ── model providers ──────────────────────────────────────────────────────────
@runtime_checkable
class LLMPort(Protocol):
    provider_id: str

    def propose(
        self,
        *,
        request: str,
        offers: list[dict],
        ontology_text: str,
        memory_text: str,
        guidance: str = "",
    ) -> dict: ...


LLMS: dict[str, LLMPort] = {}


def register_llm(llm: LLMPort) -> LLMPort:
    LLMS[llm.provider_id] = llm
    return llm


# ── channels (where a human is asked) ────────────────────────────────────────
@runtime_checkable
class ChannelPort(Protocol):
    channel_id: str

    def ask(
        self, conn, *, escalation_id: str, approver: str | None, summary: str, diff: dict
    ) -> None: ...


CHANNELS: dict[str, ChannelPort] = {}


def register_channel(channel: ChannelPort) -> ChannelPort:
    CHANNELS[channel.channel_id] = channel
    return channel


# ── the tool registry ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tool:
    name: str
    effect: str  # READ or SUBMIT. Nothing else is representable.
    merchant_id: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(
                f"tool {self.name!r} declares effect {self.effect!r}; only "
                f"{EFFECTS} exist. A tool that moves money has no place here."
            )


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)
    refused: list[dict] = field(default_factory=list)

    def add(self, tool: Tool) -> Tool:
        self.tools[f"{tool.merchant_id}.{tool.name}"] = tool
        return tool

    def refuse(self, name: str, merchant_id: str, why: str) -> None:
        """A tool we saw and will not call. Recorded, never silently dropped."""
        self.refused.append({"name": name, "merchant_id": merchant_id, "why": why})

    def callable_names(self, merchant_id: str | None = None) -> list[str]:
        return sorted(
            t.name
            for t in self.tools.values()
            if merchant_id is None or t.merchant_id == merchant_id
        )

    def assert_no_money_tools(self) -> None:
        """S2, as one assertion."""
        bad = [t for t in self.tools.values() if t.effect not in EFFECTS]
        if bad:
            raise AssertionError(f"tools with a settling effect are registered: {bad}")


TOOLS = ToolRegistry()
