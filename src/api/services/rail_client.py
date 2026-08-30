"""HTTP client for the Yuno-style AP2 orchestrator (decision 0024).

Implements `trustlib.AsyncPaymentRail`. The kernel uses two of these methods —
enrollment at mandate creation and token deletion on revocation; the merchant
uses `capture`.

One deliberate asymmetry in error handling. `delete_payment_token` is the
kill switch, and the caller has already revoked the mandate before reaching
it: a failure there must be visible but must never look like the revocation
failed. Every other method raises.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from trustlib.models import DisputeRef, Receipt, SetupToken, WebhookEvent

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class RailError(Exception):
    """The orchestrator refused or was unreachable."""

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class YunoSimRail:
    """Client for the simulated orchestrator.

    Named for what it is. Nothing here talks to Yuno; it talks to our own
    `src/yuno_sim/`, which models what a Yuno-style AP2 surface would do.
    """

    def __init__(self, *, base_url: str, timeout: httpx.Timeout | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or DEFAULT_TIMEOUT

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            response = await client.request(method, path, **kwargs)
        if response.status_code >= 400:
            body = _safe_json(response)
            raise RailError(
                f"{method} {path} -> {response.status_code}: "
                f"{body.get('message') or response.text[:200]}",
                reason_code=body.get("reason_code"),
            )
        return response.json()

    # -- enrollment --------------------------------------------------------
    async def create_setup_token(self, mandate_id: str) -> SetupToken:
        data = await self._request(
            "POST", "/v1/payment-methods/enroll", json={"mandate_id": mandate_id}
        )
        return SetupToken(**data)

    async def exchange_payment_token(self, setup_token_id: str) -> str:
        data = await self._request("POST", f"/v1/payment-methods/{setup_token_id}/confirm")
        return data["token_id"]

    # -- the kill switch ---------------------------------------------------
    async def delete_payment_token(self, token_id: str) -> None:
        """Idempotent by contract: deleting an already-deleted token is fine.

        Revocation may be retried, and a second DELETE returning an error
        would make a successful revocation look failed.
        """
        try:
            await self._request("DELETE", f"/v1/payment-methods/{token_id}")
        except RailError as exc:
            if exc.reason_code == "RAIL_TOKEN_DELETED":
                return
            raise

    # -- money -------------------------------------------------------------
    async def capture(
        self,
        *,
        token_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        intent_ref: str,
        mandate_sd_jwt: str | None = None,
        checkout_jwt: str | None = None,
    ) -> Receipt:
        """Charge the vaulted instrument.

        `mandate_sd_jwt` and `checkout_jwt` are extras beyond the frozen
        signature: they are what let the orchestrator verify the AP2 Payment
        Mandate itself before settling, instead of taking the merchant's word
        (decision 0024). Both are keyword-only and optional, so the frozen
        `PaymentRail` shape still type-checks.
        """
        payload = {
            "token_id": token_id,
            "amount": str(amount),
            "currency": currency,
            "intent_ref": intent_ref,
        }
        if mandate_sd_jwt:
            payload["mandate_sd_jwt"] = mandate_sd_jwt
        if checkout_jwt:
            payload["checkout_jwt"] = checkout_jwt

        data = await self._request(
            "POST", "/v1/payments", json=payload, headers={"Idempotency-Key": idempotency_key}
        )
        return Receipt(**data)

    async def open_dispute(self, capture_id: str, reason: str = "UNAUTHORISED") -> DisputeRef:
        data = await self._request(
            "POST", f"/v1/payments/{capture_id}/disputes", json={"reason": reason}
        )
        return DisputeRef(**data)

    # -- webhooks ----------------------------------------------------------
    def verify_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        """Verify a signed webhook. Pure computation, no I/O.

        Implemented in `merchant/webhooks.py`, which owns the merchant's view
        of the signature; the kernel does not receive rail webhooks.
        """
        raise NotImplementedError("webhook verification belongs to the merchant, not the kernel")


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
