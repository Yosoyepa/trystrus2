"""Configuration for the simulated Yuno-style AP2 orchestrator.

Separate deployable, separate settings namespace (`YUNO_*`). It shares a
database with the kernel only because this is a hackathon; nothing in the code
reads the kernel's tables, and the seam is a real HTTP boundary.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YUNO_", env_file=".env", extra="ignore"
    )

    # Identity of this simulation. Not Yuno; a proposal for what a Yuno AP2
    # surface could look like (decision 0024).
    provider_name: str = "Yuno-style AP2 orchestrator (simulated)"
    simulated: bool = True

    # The issuer whose mandates we accept. We verify against its JWKS and ask
    # it for mandate status — a credential provider that skipped the status
    # check would settle revoked mandates whose signatures still verify.
    issuer_url: str = "http://localhost:8001"
    jwks_cache_seconds: int = 300

    database_url: str = "postgresql+asyncpg:///aval"

    # Signs outgoing webhooks so the merchant can verify them (T14).
    secrets_dir: Path = REPO_ROOT / "secrets"
    webhook_key_file: str = "yuno_webhook_ed25519.pem"

    # Where the human "approves" the instrument during enrollment.
    approval_base_url: str = "http://localhost:8002/simulated-approval"


@lru_cache
def settings() -> Settings:
    return Settings()
