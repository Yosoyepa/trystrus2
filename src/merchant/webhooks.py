"""Verification of signed inbound Yuno-style simulated-rail webhooks."""

from __future__ import annotations

import json
import time

import httpx
from jwcrypto import jwk

from trustlib.canonical import canonical_json
from trustlib.jose import jwk_from_dict, peek_header, verify_detached
from trustlib.models import WebhookEvent

from .config import Settings, settings

SIGNATURE_HEADER = "x-yuno-signature"


class WebhookVerificationError(Exception):
    pass


class YunoWebhookVerifier:
    """Fetches the simulator's public JWKS and verifies each event locally."""

    def __init__(
        self, *, config: Settings | None = None, keys: dict[str, jwk.JWK] | None = None
    ) -> None:
        self._config = config or settings()
        self._keys = keys
        self._injected_keys = keys is not None
        self._fetched_at = 0.0

    async def verify(self, *, headers: dict[str, str], body: bytes) -> WebhookEvent:
        signature = _header(headers, SIGNATURE_HEADER)
        if not signature:
            raise WebhookVerificationError("missing Yuno webhook signature")
        try:
            raw = json.loads(body)
            # Event bytes are JCS by contract.  Reject noncanonical input so
            # an intermediary cannot change representation after signing.
            if canonical_json(raw) != body:
                raise ValueError("webhook body is not canonical JSON")
            event = WebhookEvent.model_validate(raw)
            kid = peek_header(signature).get("kid")
            key = (await self._verification_keys()).get(kid)
            if key is None:
                raise ValueError("unknown webhook signing key")
            verified = verify_detached(signature, event.model_dump(mode="json"), key)
            if verified != event.model_dump(mode="json"):
                raise ValueError("signed event differs from supplied event")
            return event
        except WebhookVerificationError:
            raise
        except Exception as exc:
            raise WebhookVerificationError("invalid Yuno webhook signature") from exc

    async def _verification_keys(self) -> dict[str, jwk.JWK]:
        if not self._injected_keys and (
            self._keys is None
            or time.monotonic() - self._fetched_at > self._config.yuno_jwks_cache_seconds
        ):
            try:
                async with httpx.AsyncClient(timeout=self._config.http_timeout_seconds) as client:
                    response = await client.get(
                        f"{self._config.yuno_sim_url.rstrip('/')}/.well-known/jwks.json"
                    )
                    response.raise_for_status()
                self._keys = {
                    item["kid"]: jwk_from_dict(item)
                    for item in response.json().get("keys", [])
                    if item.get("kid")
                }
                self._fetched_at = time.monotonic()
            except Exception as exc:
                raise WebhookVerificationError("Yuno webhook JWKS unavailable") from exc
        return self._keys


def _header(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None
