from src.agent.ports.setup import MerchantEndpoints


def test_rappi_endpoint_loads_from_dotenv_and_environment_wins(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TT_RAPPI_BRIDGE_URL=http://127.0.0.1:8010\n", encoding="utf-8")

    assert MerchantEndpoints(_env_file=dotenv).tt_rappi_bridge_url == "http://127.0.0.1:8010"

    monkeypatch.setenv("TT_RAPPI_BRIDGE_URL", "http://bridge.internal:8010")
    assert MerchantEndpoints(_env_file=dotenv).tt_rappi_bridge_url == "http://bridge.internal:8010"
