"""VuelaYa's narrow HTTP client for the simulated Yuno AP2 rail.

This module is intentionally merchant-local: the merchant does not import the
kernel service to reach money.  Its one `capture` method is called only by the
charge service after a verified APPROVED decision.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from trustlib.models import Receipt


class RailError(Exception):
    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class MerchantRailClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(2.0, timeout_seconds))

    async def capture(
        self,
        *,
        token_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        intent_ref: str,
        purchase_id: str,
        mandate_sd_jwt: str,
        checkout_jwt: str,
    ) -> Receipt:
        payload = {
            "token_id": token_id,
            "amount": f"{amount:.2f}",
            "currency": currency,
            "intent_ref": intent_ref,
            "purchase_id": purchase_id,
            "mandate_sd_jwt": mandate_sd_jwt,
            "checkout_jwt": checkout_jwt,
        }
        try:
            async with httpx.AsyncClient(base_url=self._base_url,
                                         timeout=self._timeout) as client:
                response = await client.post(
                    "/v1/payments", json=payload,
                    headers={"Idempotency-Key": idempotency_key},
                )
        except httpx.HTTPError as exc:
            raise RailError("Yuno-style simulated rail is unavailable") from exc

        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400:
            raise RailError(
                data.get("message") or f"rail refused with HTTP {response.status_code}",
                reason_code=data.get("reason_code"),
            )
        return Receipt.model_validate(data)
