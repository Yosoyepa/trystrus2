"""Issue, persist and verify VuelaYa's ES256 Checkout JWTs."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jwcrypto import jwk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib import ap2, ids
from trustlib.jose import (
    generate_pem_pair,
    key_from_pem,
    peek_header,
    sign_compact,
    verify_compact,
)
from trustlib.models import Offer, Receipt

from .config import Settings, settings
from .models import MerchantOrder
from .schemas import CheckoutQuote


class CheckoutJWTError(Exception):
    """The cart is not a checkout VuelaYa signed and stored."""


@dataclass(frozen=True)
class VerifiedCheckout:
    order: MerchantOrder
    payload: dict
    checkout_hash: str


class CheckoutJWTService:
    """The merchant's cart commitment.

    ES256 is mandatory here.  Ed25519 would be deterministic and AP2 forbids
    it for a checkout whose low-entropy contents could be rainbow-tabled.
    """

    def __init__(
        self, *, config: Settings | None = None, signing_key: jwk.JWK | None = None
    ) -> None:
        self._config = config or settings()
        self._signing_key = signing_key

    def _key(self) -> jwk.JWK:
        if self._signing_key is not None:
            return self._validate_key(self._signing_key)
        if self._config.gcp_project:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            secret_path = (
                f"projects/{self._config.gcp_project}/secrets/"
                f"{self._config.merchant_key_secret}/versions/latest"
            )
            self._signing_key = key_from_pem(
                client.access_secret_version(name=secret_path).payload.data
            )
            return self._validate_key(self._signing_key)
        directory = Path(self._config.secrets_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self._config.merchant_key_file
        if not path.exists():
            pem, _ = generate_pem_pair("P-256")
            path.write_bytes(pem)
            path.chmod(0o600)
        self._signing_key = key_from_pem(path.read_bytes())
        return self._validate_key(self._signing_key)

    @staticmethod
    def _validate_key(key: jwk.JWK) -> jwk.JWK:
        if key.get("kty") != "EC" or key.get("crv") != "P-256":
            raise CheckoutJWTError("merchant Checkout JWT key must be P-256")
        return key

    async def quote(self, session: AsyncSession, offer: Offer) -> CheckoutQuote:
        order_id = ids.new_id(ids.ORDER)
        payload = ap2.build_checkout_payload(
            order_id=order_id,
            merchant_id=self._config.merchant_id,
            merchant_name=self._config.merchant_name,
            merchant_website=self._config.merchant_website,
            line_items=[
                {
                    "id": offer.offer_id,
                    "label": offer.title,
                    "amount": ap2.to_minor_units(offer.amount),
                    "quantity": 1,
                }
            ],
            total_price=offer.amount,
            currency=offer.currency,
        )
        token = sign_compact(payload, self._key(), kid=self._config.merchant_kid, typ="JWT")
        digest = ap2.checkout_hash(token)
        session.add(
            MerchantOrder(
                id=order_id,
                offer_id=offer.offer_id,
                amount=offer.amount_decimal,
                currency=offer.currency,
                checkout_jwt=token,
                checkout_hash=digest,
                status="quoted",
            )
        )
        await session.flush()
        return CheckoutQuote(
            order_id=order_id, offer=offer, checkout_jwt=token, checkout_hash=digest
        )

    async def verify(self, session: AsyncSession, checkout_jwt: str) -> VerifiedCheckout:
        """Check signature, persistence and all cart facts before charging."""
        try:
            if peek_header(checkout_jwt).get("kid") != self._config.merchant_kid:
                raise CheckoutJWTError("unknown merchant checkout key")
            payload = verify_compact(checkout_jwt, self._key())
        except CheckoutJWTError:
            raise
        except Exception as exc:
            raise CheckoutJWTError("Checkout JWT signature does not verify") from exc

        order_id = payload.get("order_id")
        if not isinstance(order_id, str):
            raise CheckoutJWTError("Checkout JWT has no order id")
        # The lock is local idempotency.  It serializes concurrent attempts to
        # use the same signed cart so the second request observes the first
        # receipt rather than starting another capture.
        result = await session.execute(
            select(MerchantOrder).where(MerchantOrder.id == order_id).with_for_update()
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise CheckoutJWTError("Checkout JWT was never issued by VuelaYa")
        if not hmac.compare_digest(order.checkout_jwt, checkout_jwt):
            raise CheckoutJWTError("stored checkout bytes differ from presentation")

        digest = ap2.checkout_hash(checkout_jwt)
        if not hmac.compare_digest(order.checkout_hash, digest):
            raise CheckoutJWTError("stored checkout hash does not match presentation")
        if payload.get("merchant", {}).get("id") != self._config.merchant_id:
            raise CheckoutJWTError("Checkout JWT names another merchant")
        if payload.get("currency") != order.currency:
            raise CheckoutJWTError("Checkout JWT currency changed")
        if payload.get("total_price") != ap2.to_minor_units(order.amount):
            raise CheckoutJWTError("Checkout JWT total changed")
        return VerifiedCheckout(order=order, payload=payload, checkout_hash=digest)

    async def mark_captured(
        self,
        session: AsyncSession,
        *,
        checkout: VerifiedCheckout,
        purchase_id: str,
        receipt: Receipt,
    ) -> None:
        order = checkout.order
        if order.status == "captured" and order.purchase_id != purchase_id:
            raise CheckoutJWTError("Checkout JWT has already been captured")
        order.status = "captured"
        order.purchase_id = purchase_id
        order.receipt = receipt.model_dump(mode="json")
        order.updated_at = datetime.now(UTC)
        await session.flush()
