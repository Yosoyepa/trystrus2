"""Small, typed clients for the kernel APIs VuelaYa is allowed to call."""

from __future__ import annotations

import time
from typing import Any

import httpx
from jwcrypto import jwk

from trustlib.jose import jwk_from_dict
from trustlib.models import Decision, DecisionOutcome, ReasonCode

from .config import Settings, settings


class KernelClientError(Exception):
    pass


class IssuerJWKSClient:
    """Caches issuer public keys; it never answers mandate status itself."""

    def __init__(self, *, config: Settings | None = None) -> None:
        self._config = config or settings()
        self._keys: dict[str, jwk.JWK] | None = None
        self._fetched_at = 0.0

    async def keys(self) -> dict[str, jwk.JWK]:
        if self._keys is None or time.monotonic() - self._fetched_at > 300:
            try:
                async with httpx.AsyncClient(timeout=self._config.http_timeout_seconds) as client:
                    response = await client.get(
                        f"{self._config.kernel_url.rstrip('/')}/.well-known/jwks.json")
                    response.raise_for_status()
                self._keys = {
                    item["kid"]: jwk_from_dict(item)
                    for item in response.json().get("keys", [])
                    if item.get("kid")
                }
            except Exception as exc:
                raise KernelClientError("issuer JWKS is unavailable") from exc
            self._fetched_at = time.monotonic()
        return self._keys

    def invalidate(self) -> None:
        self._keys = None
        self._fetched_at = 0.0


class KernelVerifyClient:
    """Calls the decision owner.  It cannot approve locally."""

    def __init__(self, *, config: Settings | None = None) -> None:
        self._config = config or settings()

    async def verify(self, *, mandate_id: str, intent_jwt: str,
                     idempotency_key: str, agent_id: str) -> Decision:
        try:
            async with httpx.AsyncClient(timeout=self._config.http_timeout_seconds) as client:
                response = await client.post(
                    f"{self._config.kernel_url.rstrip('/')}/mandates/{mandate_id}/verify",
                    json={
                        "intent_jwt": intent_jwt,
                        "idempotency_key": idempotency_key,
                        "agent_id": agent_id,
                    },
                )
        except httpx.HTTPError as exc:
            raise KernelClientError("kernel verify is unavailable") from exc

        try:
            data: dict[str, Any] = response.json()
        except Exception:
            data = {}
        if response.status_code != 200:
            code = _reason_or_default(data.get("reason_code"))
            return Decision(decision=DecisionOutcome.REJECTED, reason_code=code)
        try:
            return Decision.model_validate(data)
        except Exception as exc:
            raise KernelClientError("kernel verify returned an invalid decision") from exc


class MCPPurchaseClient:
    """The deliberately narrow MCP → kernel hand-off.

    There is no amount field and this client has no reference to a payment
    rail.  The kernel remains responsible for rejecting a request that lacks
    a valid agent-signed intent; sending a request is not charging it.
    """

    def __init__(self, *, config: Settings | None = None) -> None:
        self._config = config or settings()

    async def submit(self, *, offer_id: str, mandate_jti: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._config.http_timeout_seconds) as client:
                response = await client.post(
                    f"{self._config.kernel_url.rstrip('/')}/purchases",
                    json={"offer_id": offer_id, "mandate_jti": mandate_jti},
                )
        except httpx.HTTPError as exc:
            raise KernelClientError("kernel purchase endpoint is unavailable") from exc
        if response.status_code >= 400:
            raise KernelClientError(
                "kernel rejected the purchase submission; an agent-signed "
                "intent is required before any checkout can occur")
        try:
            return response.json()
        except Exception as exc:
            raise KernelClientError("kernel returned an invalid purchase response") from exc


def _reason_or_default(value: object) -> ReasonCode:
    try:
        return ReasonCode(str(value))
    except ValueError:
        return ReasonCode.RAIL_ERROR
