"""Kernel configuration.

Key material follows decision #15's two tiers: mandate-issuing keys as PEM
(the SD-JWT library wants local key material), evidence keys in KMS. In dev the
PEM comes from a gitignored file; in GCP from Secret Manager. Nothing sensitive
lives in an environment variable or in the database.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AVAL_", env_file=".env", extra="ignore")

    # --- identity ---------------------------------------------------------
    issuer: str = "https://api.aval.example"
    issuer_kid: str = "v1"

    # --- passkeys (WebAuthn) ---------------------------------------------
    # rp_id must be a *registrable domain*. `*.run.app` is on the Public
    # Suffix List, so passkeys are rejected there — hence the day-0 domain
    # purchase (ADR-018). `localhost` works for development.
    rp_id: str = "localhost"
    rp_name: str = "Aval"
    rp_origin: str = "http://localhost:5173"
    challenge_ttl_seconds: int = 300

    # --- database ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg:///aval"

    # --- keys -------------------------------------------------------------
    # Local dev: PEM files under ./secrets (gitignored).
    # GCP: set gcp_project and the loader reads Secret Manager instead.
    secrets_dir: Path = REPO_ROOT / "secrets"
    gcp_project: str | None = None
    issuer_key_secret: str = "aval-issuer-ed25519"
    merchant_key_secret: str = "aval-merchant-es256"

    # --- the rail (decision 0024: simulated Yuno-style AP2 orchestrator) --
    aval_rail: Literal["paypal", "yuno_mock", "yuno"] = "yuno"
    yuno_sim_url: str = "http://localhost:8002"
    yuno_mock_base: str = "http://127.0.0.1:8090"
    yuno_webhook_secret: SecretStr = SecretStr("yuno-local-secret")

    # --- decision / policy / velocity (Dev 2) -----------------------------
    idem_secret: SecretStr = SecretStr("local-development-only")
    stepup_ttl_l3_s: int = 120
    stepup_ttl_l3plus_s: int = 300
    burst_intents_60s: int = 3
    burst_cooldown_s: int = 600
    escalations_h: int = 5
    open_authz_max: int = 3

    # --- human in the loop ------------------------------------------------
    escalation_timeout_seconds: int = 120  # fail closed (decision #13)

    @property
    def idem_secret_value(self) -> str:
        return self.idem_secret.get_secret_value()

    @property
    def yuno_webhook_secret_value(self) -> str:
        return self.yuno_webhook_secret.get_secret_value()


@lru_cache
def settings() -> Settings:
    return Settings()
