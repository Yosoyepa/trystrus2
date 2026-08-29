"""Typed runtime configuration for the Aval service."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings.

    Defaults are deliberately local-only values so the test suite never needs
    credentials. Deployments must provide real secrets through a secret manager.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    aval_rail: Literal["paypal", "yuno_mock", "yuno"] = Field("paypal", alias="AVAL_RAIL")
    paypal_base: str = Field("https://api-m.sandbox.paypal.com", alias="PAYPAL_BASE")
    paypal_client_id: str = Field("", alias="PAYPAL_CLIENT_ID")
    paypal_secret: SecretStr = Field(SecretStr(""), alias="PAYPAL_CLIENT_SECRET")
    webhook_id: str = Field("", alias="WEBHOOK_ID")
    idem_secret: SecretStr = Field(SecretStr("local-development-only"), alias="IDEM_SECRET")
    stepup_ttl_l3_s: int = Field(120, alias="STEPUP_TTL_L3_S", ge=1)
    stepup_ttl_l3plus_s: int = Field(300, alias="STEPUP_TTL_L3PLUS_S", ge=1)
    burst_intents_60s: int = Field(3, alias="BURST_INTENTS_60S", ge=1)
    burst_cooldown_s: int = Field(600, alias="BURST_COOLDOWN_S", ge=1)
    escalations_h: int = Field(5, alias="ESCALATIONS_H", ge=1)
    open_authz_max: int = Field(3, alias="OPEN_AUTHZ_MAX", ge=1)
    yuno_mock_base: str = Field("http://127.0.0.1:8090", alias="YUNO_MOCK_BASE")
    yuno_webhook_secret: SecretStr = Field(
        SecretStr("yuno-local-secret"), alias="YUNO_WEBHOOK_SECRET"
    )
    database_url: str = Field("sqlite:///:memory:", alias="DATABASE_URL")

    @property
    def paypal_secret_value(self) -> str:
        return self.paypal_secret.get_secret_value()

    @property
    def idem_secret_value(self) -> str:
        return self.idem_secret.get_secret_value()

    @property
    def yuno_webhook_secret_value(self) -> str:
        return self.yuno_webhook_secret.get_secret_value()
