"""Search exposes the merchant's own CDN image URLs (decision: real pictures).

Rappi answers the unified-search call with bare image paths; the bridge is the
one that stitches the CDN prefix on. Whatever the merchant returns must reach
the agent untouched — a buyer should see the same picture the Rappi app shows,
not a stand-in the platform invented.
"""

from __future__ import annotations

import json

import httpx
from src.rappi_bridge.rappi import (
    AUTH_PATH,
    IMAGES_BASE_URL,
    LazyRappiClient,
    RappiClient,
    RappiSession,
    absolute_image_url,
)

SESSION = RappiSession(token="ft.test", device_id="dev", lat="4.71", lng="-74.07")


def search_response(data: dict) -> httpx.Response:
    return httpx.Response(200, json=data, request=httpx.Request("POST", "https://x"))


def make_lazy(handler, tmp_path) -> LazyRappiClient:
    """A LazyRappiClient whose inner client uses a mocked transport."""
    from types import SimpleNamespace

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    inner = RappiClient(SESSION, base_url="https://x", transport=httpx.MockTransport(handler))
    lazy = LazyRappiClient.__new__(LazyRappiClient)
    lazy._config = SimpleNamespace(
        session_file=session_file, rappi_base_url="https://x", http_timeout_s=5.0
    )
    lazy._inner = inner
    lazy._mtime = session_file.stat().st_mtime_ns  # _ensure keeps this inner
    return lazy


UNIFIED = {
    "stores": [
        {
            "store_id": 9001,
            "store_name": "Éxito Express",
            "eta": "25 min",
            "shipping_cost": 3900,
            "logo": "stores/exitexpress.webp",
            "products": [
                {
                    "product_id": 1,
                    "name": "Botella de agua Cristal 600ml",
                    "price": 2100,
                    "image": "cristal-600.webp",
                },
                {
                    "product_id": 2,
                    "name": "Botella premium",
                    "price": 4500,
                    "image": "https://images.rappi.com/products/already-absolute.webp",
                    "images": ["extras/one.webp", "extras/two.webp"],
                },
                {"product_id": 3, "name": "Sin foto", "price": 900},
            ],
        }
    ]
}


def test_search_builds_absolute_cdn_urls(tmp_path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == AUTH_PATH:
            return httpx.Response(200, json={"id": 42}, request=request)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["body"] = json.loads(request.content)
        seen["auth_user"] = request.headers["AUTH_USER"]
        return search_response(UNIFIED)

    lazy = make_lazy(handler, tmp_path)
    results = lazy.search("botella de agua")

    assert seen == {
        "path": "/api/pns-global-search-api/v1/unified-search",
        "params": {"is_prime": "false", "unlimited_shipping": "false"},
        "body": {
            "lat": 4.71,
            "lng": -74.07,
            "query": "botella de agua",
            "options": {},
        },
        "auth_user": "42",
    }
    assert results[0]["image"] == f"{IMAGES_BASE_URL}/products/cristal-600.webp"
    assert results[0]["store_logo"] == f"{IMAGES_BASE_URL}/restaurants_logo/stores/exitexpress.webp"
    # absolute URLs pass through untouched
    assert results[1]["image"] == f"{IMAGES_BASE_URL}/products/already-absolute.webp"
    assert results[1]["images"][0] == f"{IMAGES_BASE_URL}/products/already-absolute.webp"
    assert results[1]["images"][1] == f"{IMAGES_BASE_URL}/products/extras/one.webp"
    assert results[1]["images"][2] == f"{IMAGES_BASE_URL}/products/extras/two.webp"
    # a product without a picture carries no invented one
    assert results[2]["image"] is None
    assert results[2]["images"] == []


def test_absolute_image_url_handles_edges() -> None:
    assert absolute_image_url(None) is None
    assert absolute_image_url("") is None
    assert absolute_image_url(123) is None
    assert (
        absolute_image_url("/leading/slash.png") == f"{IMAGES_BASE_URL}/products/leading/slash.png"
    )
    assert (
        absolute_image_url("http://legacy.example/p.png", prefix="restaurants_logo")
        == "http://legacy.example/p.png"
    )


def test_offer_images_normalises_merchant_fields() -> None:
    from src.agent.ports.base import normalise_offer, offer_images

    assert offer_images({"image": "a.png"}) == ["a.png"]
    assert offer_images({"image_url": "b.png", "image": "a.png"}) == ["a.png", "b.png"]
    assert offer_images({"images": ["x.png", "x.png", "y.png"]}) == ["x.png", "y.png"]
    assert offer_images({"images": "solo.png"}) == ["solo.png"]
    assert offer_images({}) == []

    offer = normalise_offer(
        {"offer_id": 1, "price": "10.00", "image_url": "https://cdn.m/p.png"},
        merchant_id="m",
    )
    assert offer["images"] == ["https://cdn.m/p.png"]


def test_rappi_adapter_passes_images_through(monkeypatch) -> None:
    from src.agent.ports.merchants_mcp import RappiBridgeMcp

    bridge = RappiBridgeMcp("http://bridge.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "store_id": "9001",
                        "store_name": "Éxito Express",
                        "sku": "1",
                        "title": "Botella de agua Cristal 600ml",
                        "price": 2100,
                        "eta": "25 min",
                        "shipping_cost": 3900,
                        "image": f"{IMAGES_BASE_URL}/products/cristal-600.webp",
                    }
                ]
            },
            request=httpx.Request("GET", "http://bridge.test/v1/rappi/search"),
        )

    def fake_get(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.get(url, **kwargs)

    monkeypatch.setattr(httpx, "get", fake_get)
    offers = bridge.search(None, query="botella de agua")

    assert offers[0]["price"] == "2100.00"
    assert offers[0]["merchant_id"] == "rappi"
    assert offers[0]["images"] == [f"{IMAGES_BASE_URL}/products/cristal-600.webp"]
    # Kernel verification obtains a fresh merchant read, never the offer
    # carried through the agent's model state.
    fresh = bridge.get(None, offers[0]["offer_id"])
    assert fresh is not None
    assert fresh["price"] == "2100.00"
    assert bridge.get(None, "rappi_9001_missing") is None
