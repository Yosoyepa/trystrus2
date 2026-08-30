"""Configuration for the VuelaYa merchant service."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MERCHANT_", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg:///aval"
    kernel_url: str = "http://localhost:8001"
    yuno_sim_url: str = "http://localhost:8002"

    merchant_id: str = "vuelaya"
    merchant_name: str = "VuelaYa"
    merchant_website: str = "https://merchant.aval.example"

    secrets_dir: Path = REPO_ROOT / "secrets"
    merchant_key_file: str = "merchant_es256.pem"
    gcp_project: str | None = None
    merchant_key_secret: str = "aval-merchant-es256"
    merchant_kid: str = "m1"

    fixtures_dir: Path = REPO_ROOT / "aval" / "contracts" / "fixtures"
    http_timeout_seconds: float = 5.0

    # Webhook signatures are public-key verified.  The cache is only for the
    # Yuno JWKS; an event signature is never accepted merely because a prior
    # event was valid.
    yuno_jwks_cache_seconds: int = 300


@lru_cache
def settings() -> Settings:
    return Settings()
