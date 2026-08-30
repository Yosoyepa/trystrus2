import httpx
from src.agent.ports.setup import MerchantEndpoints


def test_rappi_endpoint_loads_from_dotenv_and_environment_wins(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "TT_RAPPI_BRIDGE_URL=http://127.0.0.1:8010\nTT_RAPPI_BRIDGE_TOKEN=local-secret\n",
        encoding="utf-8",
    )

    assert MerchantEndpoints(_env_file=dotenv).tt_rappi_bridge_url == "http://127.0.0.1:8010"
    assert MerchantEndpoints(_env_file=dotenv).tt_rappi_bridge_token == "local-secret"

    monkeypatch.setenv("TT_RAPPI_BRIDGE_URL", "http://bridge.internal:8010")
    monkeypatch.setenv("TT_RAPPI_BRIDGE_TOKEN", "environment-secret")
    assert MerchantEndpoints(_env_file=dotenv).tt_rappi_bridge_url == "http://bridge.internal:8010"
    assert MerchantEndpoints(_env_file=dotenv).tt_rappi_bridge_token == "environment-secret"


def test_rappi_bridge_sends_tunnel_bearer_on_every_protected_call(monkeypatch) -> None:
    from src.agent.ports.merchants_mcp import RappiBridgeMcp

    seen: list[httpx.Request] = []

    def fake_get(url, **kwargs):
        request = httpx.Request(
            "GET", url, headers=kwargs.get("headers"), params=kwargs.get("params")
        )
        seen.append(request)
        return httpx.Response(200, json={"results": []}, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    bridge = RappiBridgeMcp("https://bridge.example", token="tunnel-secret")

    bridge._get("/v1/rappi/search", {"q": "agua"})  # noqa: SLF001

    assert seen[0].headers["authorization"] == "Bearer tunnel-secret"
    assert seen[0].url.params["q"] == "agua"
