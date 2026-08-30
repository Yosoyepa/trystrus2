"""Kernel capture-token verification (the other half lives in
`src/api/decision/capture_token.py`). A purchase click without a valid
token is structurally impossible: the bridge refuses to arm the checkout."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from src.trustlib.jose import jwk_from_dict, peek_header, verify_compact

from .config import BridgeConfig
from .errors import ApprovalExpired, ApprovalInvalid

TOKEN_TYP = "capture-token+jwt"
_CLOCK_SKEW_S = 5


def keys_from_jwks(jwks: dict[str, Any]) -> dict[str, Any]:
    """Map `kid -> JWK` from a standard JWKS document."""
    keys: dict[str, Any] = {}
    for entry in jwks.get("keys", []):
        kid = entry.get("kid")
        if kid:
            keys[kid] = jwk_from_dict(entry)
    return keys


def fetch_kernel_keys(
    config: BridgeConfig, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=config.http_timeout_s)
    try:
        response = http.get(config.kernel_jwks_url)
        response.raise_for_status()
        return keys_from_jwks(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise ApprovalInvalid(f"kernel JWKS unavailable: {exc}") from exc
    finally:
        if owned:
            http.close()


def verify_capture_token(
    token: str,
    *,
    keys: dict[str, Any],
    expected_purchase_id: str,
    expected_cart_hash: str,
    expected_amount: str,
    expected_dry_run: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check signature, typ, TTL window and every binding claim. Returns the
    claims on success; raises ApprovalExpired / ApprovalInvalid otherwise."""
    moment = (now or datetime.now(UTC)).timestamp()
    try:
        kid = peek_header(token).get("kid")
        typ = peek_header(token).get("typ")
    except Exception as exc:  # malformed token segment
        raise ApprovalInvalid(f"malformed capture token: {exc}") from exc
    if typ != TOKEN_TYP:
        raise ApprovalInvalid(f"unexpected token typ {typ!r}")
    key = keys.get(kid) if kid else None
    if key is None:
        raise ApprovalInvalid(f"no kernel key for kid {kid!r}")
    try:
        claims = verify_compact(token, key)
    except Exception as exc:
        raise ApprovalInvalid(f"capture token signature invalid: {exc}") from exc

    exp = claims.get("exp")
    iat = claims.get("iat")
    if not isinstance(exp, (int, float)) or moment > float(exp) + _CLOCK_SKEW_S:
        raise ApprovalExpired("capture token expired (TTL <= 120 s)")
    if not isinstance(iat, (int, float)) or float(iat) > moment + _CLOCK_SKEW_S:
        raise ApprovalInvalid("capture token issued in the future")

    if claims.get("purchase_id") != expected_purchase_id:
        raise ApprovalInvalid("capture token is bound to another purchase")
    if claims.get("cart_hash") != expected_cart_hash:
        raise ApprovalInvalid("capture token is bound to another cart — re-quote required")
    if Decimal(str(claims.get("amount"))) != Decimal(expected_amount):
        raise ApprovalInvalid("capture token amount differs from the approved one")
    if bool(claims.get("dry_run")) != bool(expected_dry_run):
        raise ApprovalInvalid("capture token dry_run flag differs from bridge config")
    return claims
