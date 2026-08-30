"""Which merchants exist in this process.

Registration is explicit and in one place, so "what can the agent reach?" is a
question with a readable answer rather than an import-time side effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import MERCHANTS, TOOLS, register_merchant
from .local import LocalMerchant, LocalRappi
from .merchants_mcp import MamiMcp, RappiBridgeMcp, VuelaYaMcp

_REPO_ROOT = Path(__file__).resolve().parents[3]


class MerchantEndpoints(BaseSettings):
    """Endpoint configuration from process env or the local `.env` file.

    The bridge is a local process in development. Reading `.env` here keeps a
    documented `TT_RAPPI_BRIDGE_URL` alive across a kernel restart, while a
    real deployment can still override it with its process environment.
    """

    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    tt_vuelaya_mcp_url: str = ""
    tt_mami_mcp_url: str = ""
    tt_rappi_bridge_url: str = ""


def configured_endpoints() -> MerchantEndpoints:
    return MerchantEndpoints()


def setup(
    *,
    local: bool = True,
    vuelaya_url: str | None = None,
    mami_url: str | None = None,
    rappi_url: str | None = None,
    quiet: bool = True,
) -> dict[str, Any]:
    """Register merchants and enumerate what each one offers.

    A remote merchant that is unreachable is skipped with a reason rather than
    crashing the process: the agent should still work with the merchants that
    are up.
    """
    MERCHANTS.clear()
    TOOLS.tools.clear()
    TOOLS.refused.clear()
    report: dict[str, Any] = {}

    if local:
        merchant = register_merchant(LocalMerchant())
        report[merchant.merchant_id] = merchant.discover()

    endpoints = configured_endpoints()
    for url, cls in (
        (vuelaya_url or endpoints.tt_vuelaya_mcp_url, VuelaYaMcp),
        (mami_url or endpoints.tt_mami_mcp_url, MamiMcp),
    ):
        if not url:
            continue
        merchant = cls(url)
        try:
            report[merchant.merchant_id] = merchant.discover()
            register_merchant(merchant)
        except Exception as exc:
            report[merchant.merchant_id] = {"unreachable": str(exc)[:200]}
            if not quiet:
                print(f"  {merchant.merchant_id}: unreachable ({exc})")

    rappi_url = rappi_url or endpoints.tt_rappi_bridge_url
    rappi_live = False
    if rappi_url:
        merchant = RappiBridgeMcp(rappi_url)
        try:
            report["rappi"] = merchant.discover()
            register_merchant(merchant)
            rappi_live = True
        except Exception as exc:
            report["rappi"] = {"unreachable": str(exc)[:200], "fallback": "fixture"}
            if not quiet:
                print(f"  rappi: unreachable ({exc}); using fixture catalog")
    if not rappi_live:
        fixture = register_merchant(LocalRappi())
        report.setdefault("rappi", {"fixture": True})
        report["rappi"]["tools"] = fixture.discover().get("tools")
    return report
