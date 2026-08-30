"""The composed kernel must reach Rappi without exposing the bridge to the LAN."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename", ["compose.yaml", "docker-compose.yml"])
def test_rappi_bridge_uses_private_compose_network(filename: str) -> None:
    compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    services = compose["services"]
    bridge = services["rappi_bridge"]
    kernel = services["kernel"]

    assert "env_file" not in bridge
    assert kernel["environment"]["TT_RAPPI_BRIDGE_URL"].endswith("http://rappi_bridge:8010}")
    assert kernel["depends_on"]["rappi_bridge"]["condition"] == "service_healthy"
    assert bridge["ports"] == ["127.0.0.1:8010:8010"]
    assert bridge["environment"]["AVAL_BRIDGE_DRY_RUN"].endswith("true}")
    assert "./secrets:/app/secrets" in bridge["volumes"]
    assert "./var/rappi-bridge:/app/var/rappi-bridge" in bridge["volumes"]
