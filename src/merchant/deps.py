"""Merchant-owned singletons.  Tests replace them without starting services."""

from __future__ import annotations

from .charge import ChargeService
from .checkout_jwt import CheckoutJWTService
from .config import settings
from .kernel_client import IssuerJWKSClient, KernelVerifyClient
from .rail_client import MerchantRailClient

_jwks: IssuerJWKSClient | None = None
_verify: KernelVerifyClient | None = None
_checkout: CheckoutJWTService | None = None
_rail: MerchantRailClient | None = None
_charge: ChargeService | None = None


def jwks() -> IssuerJWKSClient:
    global _jwks
    if _jwks is None:
        _jwks = IssuerJWKSClient()
    return _jwks


def verify_client() -> KernelVerifyClient:
    global _verify
    if _verify is None:
        _verify = KernelVerifyClient()
    return _verify


def checkout() -> CheckoutJWTService:
    global _checkout
    if _checkout is None:
        _checkout = CheckoutJWTService()
    return _checkout


def rail() -> MerchantRailClient:
    global _rail
    if _rail is None:
        config = settings()
        _rail = MerchantRailClient(
            base_url=config.yuno_sim_url, timeout_seconds=config.http_timeout_seconds
        )
    return _rail


def charge_service() -> ChargeService:
    global _charge
    if _charge is None:
        _charge = ChargeService(
            jwks=jwks(),
            verify_client=verify_client(),
            checkout=checkout(),
            rail=rail(),
            merchant_id=settings().merchant_id,
        )
    return _charge


def reset() -> None:
    global _jwks, _verify, _checkout, _rail, _charge
    _jwks = _verify = _checkout = _rail = _charge = None
