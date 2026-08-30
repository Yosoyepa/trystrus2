"""Signed outbound events from the simulated Yuno-style orchestrator.

The rail signs a canonical `EventEnvelope` with Ed25519.  The merchant uses
only the public JWKS exposed by this service; a caller cannot forge an event
merely because it can reach the webhook endpoint.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from jwcrypto import jwk

from trustlib import ids
from trustlib.canonical import canonical_json
from trustlib.jose import generate_pem_pair, key_from_pem, public_jwk, sign_detached
from trustlib.models import EventEnvelope

from .config import Settings, settings

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Yuno-Signature"


class WebhookSigner:
    def __init__(self, *, config: Settings | None = None, key: jwk.JWK | None = None) -> None:
        self._config = config or settings()
        self._key = key

    def _signing_key(self) -> jwk.JWK:
        if self._key is not None:
            return self._validate_key(self._key)
        if self._config.gcp_project:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            secret_path = (
                f"projects/{self._config.gcp_project}/secrets/"
                f"{self._config.webhook_key_secret}/versions/latest"
            )
            self._key = key_from_pem(client.access_secret_version(name=secret_path).payload.data)
            return self._validate_key(self._key)
        directory = Path(self._config.secrets_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self._config.webhook_key_file
        if not path.exists():
            pem, _ = generate_pem_pair("Ed25519")
            path.write_bytes(pem)
            path.chmod(0o600)
        self._key = key_from_pem(path.read_bytes())
        return self._validate_key(self._key)

    @staticmethod
    def _validate_key(key: jwk.JWK) -> jwk.JWK:
        if key.get("kty") != "OKP" or key.get("crv") != "Ed25519":
            raise ValueError("Yuno webhook key must be Ed25519")
        return key

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        key = public_jwk(self._signing_key(), kid=self._config.webhook_kid)
        key.update({"use": "sig", "alg": "EdDSA"})
        return {"keys": [key]}

    def event(
        self, *, type: str, payload: dict[str, Any], aggregate_id: str | None = None
    ) -> EventEnvelope:
        return EventEnvelope(
            event_id=ids.new_id(ids.EVENT),
            type=type,
            aggregate_id=aggregate_id or payload.get("capture_id") or "yuno_sim",
            payload=payload,
            created_at=datetime.now(UTC),
        )

    def serialize(self, event: EventEnvelope) -> tuple[bytes, dict[str, str]]:
        payload = event.model_dump(mode="json")
        body = canonical_json(payload)
        signature = sign_detached(payload, self._signing_key(), kid=self._config.webhook_kid)
        return body, {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: signature,
        }


_signer: WebhookSigner | None = None


def signer() -> WebhookSigner:
    global _signer
    if _signer is None:
        _signer = WebhookSigner()
    return _signer


async def deliver(event: EventEnvelope, *, signer_instance: WebhookSigner | None = None) -> bool:
    """Deliver after the business transaction commits; unavailable is visible.

    Delivery is deliberately not a prerequisite for settlement.  The rail's
    durable record exists independently, and the merchant will get a retrying
    outbox in the production hardening path.  Here we never fake success.
    """
    active_signer = signer_instance or signer()
    config = active_signer._config
    if not config.merchant_webhook_url:
        log.info("no merchant webhook configured; signed %s retained locally", event.type)
        return False
    body, headers = active_signer.serialize(event)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(config.merchant_webhook_url, content=body, headers=headers)
        response.raise_for_status()
    except Exception:
        log.warning("Yuno webhook delivery failed for %s", event.event_id, exc_info=True)
        return False
    return True
