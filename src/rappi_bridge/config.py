"""Bridge configuration. Everything sensitive comes from the environment or
files that stay on this machine (decision 0030, invariant 3)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class BridgeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVAL_BRIDGE_", env_file=".env", extra="ignore"
    )

    rappi_base_url: str = "https://services.grability.rappi.com"
    # Session file written by the audited CLI login (`rappi login`) or the
    # manual setup; custody rules: chmod 600, gitignored, never uploaded.
    session_file: Path = Path("secrets/rappi-config.json")
    kernel_jwks_url: str = "http://127.0.0.1:8001/.well-known/jwks.json"
    # Defense in depth: a cap the bridge enforces even against a valid
    # kernel approval (protects against a misconfigured mandate, not just a
    # compromised agent).
    max_order_cop: str = "50000.00"
    # DRY_RUN by default (decision 0030): the paying click becomes a no-op.
    dry_run: bool = True
    # Kill switch: AVAL_BRIDGE_ENABLED=0 makes every tool answer with an
    # audited BRIDGE_DISABLED error.
    enabled: bool = True
    state_db_path: Path = Path("var/rappi-bridge/state.sqlite3")
    bind: str = "127.0.0.1"
    port: int = 8010
    # Optional shared secret for kernel->bridge calls over a tunnel
    # (topology B). Local-only topology leaves it unset.
    local_token: str | None = None
    http_timeout_s: float = 15.0
    quote_ttl_s: int = 300
    action_budget: int = 24
    # OTP login capture ("Config Rappi"): "chrome" uses the owner's installed
    # Google Chrome (falls back to Playwright's chromium); "" forces chromium.
    login_browser: str = "chrome"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cap(self) -> Decimal:
        return Decimal(self.max_order_cop).quantize(Decimal("0.01"))
