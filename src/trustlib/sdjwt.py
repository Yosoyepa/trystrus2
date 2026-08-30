"""SD-JWT issuance and verification (schemas.md §1).

Format: issuer-signed JWT, then `~`-separated disclosures, then an optional
Key Binding JWT appended by the *holder* (the agent) at presentation time:

    <issuer-jwt>~<disclosure>~<disclosure>~<kb-jwt>

Selective disclosure lets the agent reveal `shipping_address` or `email` only
when a merchant actually requires them. Key binding is what proves the
presenter holds the `cnf.jwk` private key -- an impersonated agent dies here,
before any call to us (decision #6).

We implement the wire format directly rather than through the `sd-jwt`
library's issuer/holder/verifier classes: we need exact control over the
canonical bytes being signed, and the hash-binding rules for AP2 are ours.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from jwcrypto import jwk
from jwcrypto.jws import InvalidJWSSignature

from .canonical import canonical_json
from .jose import (
    b64u_decode,
    b64u_encode,
    jwk_from_dict,
    peek_header,
    sign_compact,
    verify_compact,
)

SD_ALG = "sha-256"
SEPARATOR = "~"
KB_TYP = "kb+jwt"


class SDJWTError(Exception):
    """Base for every SD-JWT failure -- callers map these to ReasonCodes."""


class InvalidSignature(SDJWTError):
    """Issuer signature does not verify against the JWKS."""


class Expired(SDJWTError):
    """`exp` is in the past."""


class NotYetValid(SDJWTError):
    """`nbf` is in the future."""


class InvalidKeyBinding(SDJWTError):
    """Missing/invalid KB-JWT, wrong nonce, or wrong audience."""


# ==========================================================================
# Disclosures
# ==========================================================================
def _digest(disclosure: str) -> str:
    return b64u_encode(hashlib.sha256(disclosure.encode("ascii")).digest())


def make_disclosure(name: str, value: Any) -> tuple[str, str]:
    """Return `(disclosure, digest)` for one selectively-disclosable claim.

    A disclosure is `base64url([salt, name, value])`; the issuer publishes only
    the digest, so the claim's presence is provable but its content is not
    revealed until the holder chooses.
    """
    salt = b64u_encode(secrets.token_bytes(16))
    disclosure = b64u_encode(canonical_json([salt, name, value]))
    return disclosure, _digest(disclosure)


# ==========================================================================
# Issue
# ==========================================================================
def issue(
    claims: dict[str, Any],
    key: jwk.JWK,
    *,
    kid: str,
    selective: dict[str, Any] | None = None,
) -> str:
    """Sign `claims` as an SD-JWT, hiding `selective` behind digests.

    Returns `<jwt>~<disclosure>~...~` (trailing separator, per the RFC, marks
    that no KB-JWT is attached yet -- the holder appends one).
    """
    payload = dict(claims)
    disclosures: list[str] = []

    if selective:
        digests = []
        for name, value in selective.items():
            disclosure, digest = make_disclosure(name, value)
            disclosures.append(disclosure)
            digests.append(digest)
        payload["_sd"] = digests
        payload["_sd_alg"] = SD_ALG

    token = sign_compact(payload, key, kid=kid, typ="sd+jwt")
    return SEPARATOR.join([token, *disclosures]) + SEPARATOR


# ==========================================================================
# Key binding (holder side -- the agent, Dev 1)
# ==========================================================================
def attach_key_binding(
    sd_jwt: str,
    holder_key: jwk.JWK,
    *,
    nonce: str,
    aud: str,
) -> str:
    """Append a KB-JWT signed by the holder, binding this presentation.

    `sd_hash` covers everything presented so far, so a KB-JWT cannot be lifted
    onto a different set of disclosures.
    """
    presented = sd_jwt if sd_jwt.endswith(SEPARATOR) else sd_jwt + SEPARATOR
    sd_hash = b64u_encode(hashlib.sha256(presented.encode("ascii")).digest())

    kb = sign_compact(
        {
            "nonce": nonce,
            "aud": aud,
            "iat": int(time.time()),
            "sd_hash": sd_hash,
        },
        holder_key,
        typ=KB_TYP,
    )
    return presented + kb


# ==========================================================================
# Verify
# ==========================================================================
def _split(sd_jwt: str) -> tuple[str, list[str], str | None]:
    parts = sd_jwt.split(SEPARATOR)
    issuer_jwt = parts[0]
    rest = parts[1:]
    # A trailing empty element means "no KB-JWT".
    kb = rest.pop() if rest and rest[-1] != "" else None
    if rest and rest[-1] == "":
        rest.pop()
    return issuer_jwt, [d for d in rest if d], kb


def verify(
    sd_jwt: str,
    keys: dict[str, jwk.JWK],
    *,
    nonce: str | None = None,
    aud: str | None = None,
    require_key_binding: bool = False,
    now: int | None = None,
    leeway: int = 0,
) -> dict[str, Any]:
    """Verify an SD-JWT and return its claims with disclosures merged in.

    Order matters and mirrors schemas.md §2: issuer signature, then temporal
    freshness, then key binding. A caller that stops early would accept an
    expired-but-well-signed mandate.

    `keys` maps `kid` -> public JWK (the JWKS, current + previous).
    """
    issuer_jwt, disclosures, kb_jwt = _split(sd_jwt)

    # 1. issuer signature
    header = peek_header(issuer_jwt)
    kid = header.get("kid")
    key = keys.get(kid) if kid else next(iter(keys.values()), None)
    if key is None:
        raise InvalidSignature(f"no key for kid={kid!r}")
    try:
        claims = verify_compact(issuer_jwt, key)
    except (InvalidJWSSignature, ValueError) as exc:
        raise InvalidSignature("issuer signature does not verify") from exc

    # 2. temporal window
    current = now if now is not None else int(time.time())
    if "exp" in claims and current > claims["exp"] + leeway:
        raise Expired(f"exp={claims['exp']} < now={current}")
    if "nbf" in claims and current + leeway < claims["nbf"]:
        raise NotYetValid(f"nbf={claims['nbf']} > now={current}")

    # 3. disclosures -- each must match a digest the issuer signed
    if disclosures:
        signed = set(claims.get("_sd", []))
        for disclosure in disclosures:
            if _digest(disclosure) not in signed:
                raise InvalidSignature("disclosure does not match any signed digest")
            _, name, value = json.loads(b64u_decode(disclosure))
            claims[name] = value

    # 4. key binding
    if require_key_binding or kb_jwt:
        if not kb_jwt:
            raise InvalidKeyBinding("key binding required but absent")
        cnf = claims.get("cnf") or {}
        holder_jwk = cnf.get("jwk")
        if not holder_jwk:
            raise InvalidKeyBinding("mandate carries no cnf.jwk to bind against")
        try:
            kb = verify_compact(kb_jwt, jwk_from_dict(holder_jwk))
        except (InvalidJWSSignature, ValueError) as exc:
            raise InvalidKeyBinding("KB-JWT signature does not verify") from exc

        if nonce is not None and kb.get("nonce") != nonce:
            raise InvalidKeyBinding("KB-JWT nonce mismatch")
        if aud is not None and kb.get("aud") != aud:
            raise InvalidKeyBinding("KB-JWT audience mismatch")

        presented = issuer_jwt
        if disclosures:
            presented += SEPARATOR + SEPARATOR.join(disclosures)
        presented += SEPARATOR
        expected = b64u_encode(hashlib.sha256(presented.encode("ascii")).digest())
        if kb.get("sd_hash") != expected:
            raise InvalidKeyBinding("KB-JWT sd_hash does not cover this presentation")

    claims.pop("_sd", None)
    claims.pop("_sd_alg", None)
    return claims
