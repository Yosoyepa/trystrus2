"""Payment rail -- PayPal-sandbox shaped, local and free.

MOCK BOUNDARY (Dev 3's lane, decision #8).  Real in this mock: the vaulted-token
model (the agent only ever sees an opaque `token_ref`, never a card), capture
idempotency keyed like PayPal's `PayPal-Request-Id` (M1), token DELETE as the
rail-side kill switch (M8), and a dispute object.  Swapping in real PayPal REST
means replacing four function bodies; the call shape does not change.
"""

from __future__ import annotations

import json
from typing import Any

from ..crypto.money import fmt
from ..ids import new_id, now_iso


class RailError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def vault_instrument(conn, mandate_jti: str, label: str = "visa-1111") -> str:
    """Person approves the instrument once; we keep an opaque reference (O7)."""
    token_ref = new_id("ppt")
    conn.execute(
        "INSERT INTO payment_instruments(token_ref,mandate_jti,rail,status,created_at)"
        " VALUES(?,?,'paypal_sandbox_mock','active',?)",
        (token_ref, mandate_jti, now_iso()),
    )
    return token_ref


def delete_token(conn, token_ref: str) -> dict[str, Any]:
    cur = conn.execute(
        "UPDATE payment_instruments SET status='deleted' WHERE token_ref=? AND status='active'",
        (token_ref,),
    )
    return {"token_ref": token_ref, "deleted": cur.rowcount > 0}


def capture(conn, *, token_ref: str, amount: str, currency: str, request_id: str) -> dict[str, Any]:
    """Server-to-server capture against the vaulted token.

    Same `request_id` twice returns the original result rather than charging
    again (M1).  A deleted token fails -- this is what makes revocation bite at
    the rail even if our own state were somehow stale (M8).
    """
    prior = conn.execute(
        "SELECT response FROM idempotency_keys WHERE key=? AND scope='rail.capture'", (request_id,)
    ).fetchone()
    if prior:
        return {**json.loads(prior["response"]), "idempotent_replay": True}

    row = conn.execute(
        "SELECT * FROM payment_instruments WHERE token_ref=?", (token_ref,)
    ).fetchone()
    if row is None:
        raise RailError("RAIL_ERROR", f"unknown payment token {token_ref}")
    if row["status"] != "active":
        raise RailError("RAIL_TOKEN_DELETED", "payment token was deleted at the rail (revocation)")

    # Test cards, mirroring the dummy-connector convention.
    if fmt(amount) == "0.00":
        raise RailError("RAIL_ERROR", "zero amount refused by the rail")

    result = {
        "capture_id": new_id("cap"),
        "status": "COMPLETED",
        "amount": fmt(amount),
        "currency": currency,
        "token_ref": token_ref,
        "captured_at": now_iso(),
    }
    conn.execute(
        "INSERT INTO idempotency_keys(key,scope,response,created_at) VALUES(?,?,?,?)",
        (request_id, "rail.capture", json.dumps(result), now_iso()),
    )
    return result


def open_dispute(conn, capture_id: str, reason: str = "UNAUTHORISED") -> dict[str, Any]:
    """The bonus flow: 'I never authorised this'. Answered with the chain."""
    return {
        "dispute_id": new_id("dsp"),
        "capture_id": capture_id,
        "reason": reason,
        "status": "OPEN",
        "opened_at": now_iso(),
    }
