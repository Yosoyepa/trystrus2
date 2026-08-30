"""Kernel-minted capture tokens (decision 0030).

The bridge refuses to arm a Rappi checkout without one of these: a compact
JWS, `typ=capture-token+jwt`, TTL <= the L3 step-up window, binding the
purchase, its reservation, the approved amount, the quoted cart and the
DRY_RUN flag. The human step-up approval is what releases the mint; a token
expired or bound to another cart is useless at the bridge.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.trustlib.jose import sign_compact

TOKEN_TYP = "capture-token+jwt"
DEFAULT_TTL_S = 120  # == stepup_ttl_l3_s: the approval window IS the token TTL


def mint_capture_token(
    *,
    purchase_id: str,
    reservation_id: str | None,
    amount: str,
    cart_hash: str,
    key: Any,
    kid: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_S,
    now: datetime | None = None,
    dry_run: bool = False,
) -> str:
    """Sign the capture binding with the kernel issuer key."""
    moment = int((now or datetime.now(UTC)).timestamp())
    claims: dict[str, Any] = {
        "purchase_id": purchase_id,
        "reservation_id": reservation_id,
        "amount": str(amount),
        "cart_hash": cart_hash,
        "dry_run": bool(dry_run),
        "iat": moment,
        "exp": moment + ttl_seconds,
    }
    return sign_compact(claims, key, kid=kid, typ=TOKEN_TYP)
