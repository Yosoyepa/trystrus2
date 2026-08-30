"""Which merchants exist in this process.

Registration is explicit and in one place, so "what can the agent reach?" is a
question with a readable answer rather than an import-time side effect.
"""
from __future__ import annotations
import os
from typing import Any

from .base import MERCHANTS, TOOLS
from .local import LocalMerchant
from .merchants_mcp import MamiMcp, VuelaYaMcp
from .base import register_merchant

VUELAYA_MCP_URL = os.environ.get("TT_VUELAYA_MCP_URL", "")
MAMI_MCP_URL = os.environ.get("TT_MAMI_MCP_URL", "")


def setup(*, local: bool = True, vuelaya_url: str | None = None,
          mami_url: str | None = None, quiet: bool = True) -> dict[str, Any]:
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

    for url, cls in ((vuelaya_url or VUELAYA_MCP_URL, VuelaYaMcp),
                     (mami_url or MAMI_MCP_URL, MamiMcp)):
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
    return report
