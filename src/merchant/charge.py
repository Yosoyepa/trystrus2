"""The seven-step, fail-closed VuelaYa checkout path."""

from __future__ import annotations

import time
from decimal import InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from trustlib import ap2, sdjwt
from trustlib.jose import jwk_from_dict, verify_detached
from trustlib.models import DecisionOutcome, MandateClaims, Offer, ReasonCode, Receipt

from . import catalog
from .checkout_jwt import CheckoutJWTError, CheckoutJWTService
from .kernel_client import IssuerJWKSClient, KernelClientError, KernelVerifyClient
from .rail_client import MerchantRailClient, RailError
from .schemas import ChargeRequest


class ChargeRefused(Exception):
    """A normal 402 outcome.  The rail has not been invoked at this point."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class ChargeSettlementError(Exception):
    """The rail may have captured, but VuelaYa could not finish recording it.

    This is deliberately distinct from a 402.  A caller must retry with the
    same purchase id instead of treating an uncertain settlement as a denial
    and opening a fresh purchase.
    """


class ChargeService:
    def __init__(
        self,
        *,
        jwks: IssuerJWKSClient,
        verify_client: KernelVerifyClient,
        checkout: CheckoutJWTService,
        rail: MerchantRailClient,
        merchant_id: str = "vuelaya",
    ) -> None:
        self._jwks = jwks
        self._verify_client = verify_client
        self._checkout = checkout
        self._rail = rail
        self._merchant_id = merchant_id

    async def charge(self, session: AsyncSession, body: ChargeRequest) -> Receipt:
        # 1. SD-JWT issuer signature, verified locally from the public JWKS.
        claims = await self._verify_mandate(body.mandate_sd_jwt)

        # 2. Detached agent intent proof-of-possession against `cnf.jwk`.
        self._verify_intent(body, claims)

        # 3. Current merchant catalogue price, never an agent-proposed amount.
        offer = await catalog.get_offer(session, body.intent.offer_id)
        if offer is None:
            raise ChargeRefused(
                ReasonCode.CONDITION_FAILED, "offer is not active in VuelaYa's catalogue"
            )
        self._verify_price(body, offer)

        # 4. The agent's hash binds the purchase to the exact stored cart.
        try:
            checkout = await self._checkout.verify(session, body.checkout_jwt)
        except CheckoutJWTError as exc:
            raise ChargeRefused(ReasonCode.CONDITION_FAILED, str(exc)) from exc
        if body.intent.checkout_hash is None or not ap2.verify_checkout_binding(
            checkout_jwt=body.checkout_jwt, claimed_hash=body.intent.checkout_hash
        ):
            raise ChargeRefused(
                ReasonCode.CONDITION_FAILED, "intent is not bound to this Checkout JWT"
            )
        if checkout.order.offer_id != offer.offer_id:
            raise ChargeRefused(ReasonCode.CONDITION_FAILED, "Checkout JWT names another offer")
        if checkout.order.amount != offer.amount_decimal:
            raise ChargeRefused(
                ReasonCode.CONDITION_FAILED, "quoted checkout no longer matches catalogue price"
            )
        if checkout.order.status == "captured":
            if checkout.order.purchase_id != body.purchase_id:
                raise ChargeRefused(
                    ReasonCode.CONDITION_FAILED,
                    "Checkout JWT was already captured for another purchase",
                )
            if checkout.order.receipt is None:
                raise ChargeSettlementError(
                    "Checkout JWT is marked captured but its receipt is missing"
                )
            try:
                return Receipt.model_validate(checkout.order.receipt)
            except Exception as exc:
                raise ChargeSettlementError("stored Checkout JWT receipt is invalid") from exc
        if checkout.order.status != "quoted":
            raise ChargeRefused(
                ReasonCode.CONDITION_FAILED, "Checkout JWT is not available for capture"
            )

        # 5. The decision owner performs the live, atomic policy verification.
        try:
            decision = await self._verify_client.verify(
                mandate_id=body.mandate_id,
                intent_jwt=body.intent_jwt,
                idempotency_key=body.idempotency_key or body.purchase_id,
                agent_id=body.intent.agent,
            )
        except KernelClientError as exc:
            raise ChargeRefused(ReasonCode.RAIL_ERROR, str(exc)) from exc
        if decision.decision is not DecisionOutcome.APPROVED:
            raise ChargeRefused(
                decision.reason_code or ReasonCode.RAIL_ERROR,
                "kernel verify did not approve this purchase",
            )

        # 6. The sole route to money.  No earlier branch above calls the rail.
        try:
            receipt = await self._rail.capture(
                token_id=body.payment_method_ref,
                amount=offer.amount_decimal,
                currency=offer.currency,
                idempotency_key=body.idempotency_key or body.purchase_id,
                intent_ref=body.intent.jti,
                purchase_id=body.purchase_id,
                mandate_sd_jwt=body.mandate_sd_jwt,
                checkout_jwt=body.checkout_jwt,
            )
        except RailError as exc:
            raise ChargeRefused(_rail_reason(exc), str(exc)) from exc

        # 7. Persist the receipt against the exact order that was charged.
        try:
            await self._checkout.mark_captured(
                session,
                checkout=checkout,
                purchase_id=body.purchase_id,
                receipt=receipt,
            )
        except CheckoutJWTError as exc:
            # This is after a rail success, so never make it look like a
            # normal denial.  The client must retry the same purchase id.
            raise ChargeSettlementError(str(exc)) from exc
        return receipt.model_copy(update={"purchase_id": body.purchase_id})

    async def _verify_mandate(self, token: str) -> MandateClaims:
        try:
            claims = sdjwt.verify(token, await self._jwks.keys())
            return MandateClaims.model_validate(claims)
        except (KernelClientError, sdjwt.SDJWTError, ValueError) as exc:
            raise ChargeRefused(
                ReasonCode.INVALID_SIGNATURE, "mandate SD-JWT does not verify"
            ) from exc

    def _verify_intent(self, body: ChargeRequest, claims: MandateClaims) -> None:
        intent = body.intent
        try:
            signed = verify_detached(
                body.intent_jwt, intent.model_dump(mode="json"), jwk_from_dict(claims.cnf.jwk)
            )
            if signed != intent.model_dump(mode="json"):
                raise ValueError("detached payload differs from supplied intent")
            now = int(time.time())
            if intent.exp - intent.iat > 120 or intent.exp < now or intent.iat > now + 5:
                raise ValueError("intent is not fresh")
            if intent.mandate_jti != claims.jti or intent.agent != claims.agent:
                raise ValueError("intent is not bound to this mandate and agent")
            if intent.merchant_id != self._merchant_id or intent.currency != claims.currency:
                raise ValueError("intent names a different merchant or currency")
            if (
                not claims.payment_method_ref
                or body.payment_method_ref != claims.payment_method_ref
            ):
                raise ValueError("payment token differs from the signed mandate")
        except Exception as exc:
            raise ChargeRefused(
                ReasonCode.INVALID_PROOF_OF_POSSESSION, "agent detached JWS proof does not verify"
            ) from exc

    @staticmethod
    def _verify_price(body: ChargeRequest, offer: Offer) -> None:
        try:
            same = (
                body.intent.amount_decimal == offer.amount_decimal
                and body.amount_decimal == offer.amount_decimal
                and body.currency == offer.currency
            )
        except (InvalidOperation, ValueError) as exc:
            raise ChargeRefused(
                ReasonCode.CONDITION_FAILED, "amount is not a valid merchant price"
            ) from exc
        if not same:
            raise ChargeRefused(
                ReasonCode.CONDITION_FAILED, "intent amount must equal the current offer price"
            )


def _rail_reason(exc: RailError) -> ReasonCode:
    try:
        return ReasonCode(exc.reason_code) if exc.reason_code else ReasonCode.RAIL_ERROR
    except ValueError:
        return ReasonCode.RAIL_ERROR
