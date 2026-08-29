"""Mandate issuance and verification (C1, C2, C8, C9).

MOCK BOUNDARY -- this stands in for Dev 3's kernel identity lane.  What is real
here: the Ed25519 signature, the agent key binding in `cnf.jwk`, the claim shape
from `contracts/schemas.md` section 1, and offline verification against a
published JWKS.  What is mocked: the passkey ceremony (a passkey cannot be
driven from a terminal, so `signed_with` records the intended ceremony) and
SD-JWT selective disclosure (claims are carried whole).

AP2 alignment: this object is an IntentMandate in AP2 terms -- the
human-not-present pre-authorisation carrying constraints and an expiry.  See
`docs/PROTOCOLS.md`.
"""
from __future__ import annotations
import json
from typing import Any

from . import audit
from .config import ISSUER
from .crypto import jws
from .crypto.keys import jwk_to_public, load_or_create, public_jwk
from .crypto.money import fmt
from .ids import new_id, now_iso, now_ts

ISSUER_KEY_NAME = "issuer"
KID = "v1"


def jwks() -> dict[str, Any]:
    """What a merchant fetches to verify us without ever calling our API (C8).

    C9: publish current + previous during rotation with a 24h grace window.
    """
    return {"keys": [public_jwk(load_or_create(ISSUER_KEY_NAME), kid=KID)]}


def issue(conn, *, user_id: str, agent_id: str, agent_jwk: dict,
          payment_method_ref: str, scope: dict, conditions: dict, limits: dict,
          validity: dict, currency: str = "USD", parent_jti: str | None = None,
          signed_with: str = "passkey(mock)") -> dict[str, Any]:
    jti = new_id("mdt")
    issued = now_ts()
    claims = {
        "iss": ISSUER,
        "iat": issued,
        "nbf": issued,
        "exp": int(validity.get("exp", issued + 30 * 24 * 3600)),
        "jti": jti,
        "type": "purchase_mandate_v1",
        "sub": user_id,
        "agent": agent_id,
        "cnf": {"jwk": agent_jwk},
        "payment_method_ref": payment_method_ref,
        "currency": currency,
        "scope": scope,
        "conditions": conditions,
        "limits": {
            "max_per_txn": fmt(limits["max_per_txn"]),
            "total_budget": fmt(limits["total_budget"]),
            "max_txn": limits.get("max_txn", {"count": 3, "period": "month"}),
        },
        "validity": validity,
        "signed_with": signed_with,
    }
    if parent_jti:
        claims["parent_jti"] = parent_jti
    token = jws.sign_compact(claims, load_or_create(ISSUER_KEY_NAME), kid=KID,
                             typ="mandate+jwt")
    stamp = now_iso()
    conn.execute(
        "INSERT INTO mandates(jti,user_id,agent_id,status,claims,token,parent_jti,"
        "created_at,updated_at) VALUES(?,?,?,'active',?,?,?,?,?)",
        (jti, user_id, agent_id, json.dumps(claims), token, parent_jti, stamp, stamp),
    )
    conn.execute(
        "INSERT INTO payment_instruments(token_ref,mandate_jti,rail,status,"
        "created_at) VALUES(?,?,'paypal_sandbox_mock','active',?) "
        "ON CONFLICT (token_ref) DO NOTHING",
        (payment_method_ref, jti, stamp),
    )
    audit.append(conn, "mandate.created",
                 {"jti": jti, "sub": user_id, "agent": agent_id, "limits": claims["limits"],
                  "scope": scope, "conditions": conditions, "signed_with": signed_with,
                  "parent_jti": parent_jti},
                 actor=user_id, agent_id=agent_id, mandate_jti=jti)
    return {"jti": jti, "token": token, "claims": claims}


def verify_token(token: str) -> dict[str, Any]:
    """Offline verification against the published JWKS (C1, C8).

    A merchant does exactly this: it never has to believe our answer, because it
    checks the cryptography itself.  One mutated byte fails here.
    """
    key = jwk_to_public(jwks()["keys"][0])
    return jws.verify_compact(token, key)


def agent_key_from_mandate(claims: dict) -> Any:
    """The agent public key BOUND INTO the mandate (C2).

    An impersonator without the private half cannot produce a valid intent, so
    impersonation dies at the signature -- before any state is consulted.
    """
    return jwk_to_public(claims["cnf"]["jwk"])


def revoke(conn, jti: str, *, actor: str, reason: str = "revoked by holder") -> dict:
    """Revocation kills the mandate AND the rail token, so the next attempt
    fails twice: once in our state, once at the rail (M8)."""
    stamp = now_iso()
    conn.execute("UPDATE mandates SET status='revoked', updated_at=? WHERE jti=?",
                 (stamp, jti))
    row = conn.execute("SELECT claims FROM mandates WHERE jti=?", (jti,)).fetchone()
    token_ref = json.loads(row["claims"])["payment_method_ref"] if row else None
    from .mocks.rail import delete_token
    rail = delete_token(conn, token_ref) if token_ref else {"deleted": False}
    audit.append(conn, "mandate.revoked",
                 {"jti": jti, "by": actor, "reason": reason, "rail": rail},
                 actor=actor, mandate_jti=jti)
    return {"jti": jti, "status": "revoked", "rail_token_deleted": rail.get("deleted")}


def get(conn, jti: str):
    row = conn.execute("SELECT * FROM mandates WHERE jti=?", (jti,)).fetchone()
    if row is None:
        raise KeyError(f"no such mandate: {jti}")
    return row
