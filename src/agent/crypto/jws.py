"""JWS EdDSA, compact and detached (C1, C3, C4).

Detached: the payload travels next to the signature rather than inside it, so
the verifier re-canonicalises the object it actually received and checks that.
"""
from __future__ import annotations
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import canonical_bytes
from .keys import b64u, b64u_decode


class BadSignature(Exception):
    pass


def _header(kid: str | None, typ: str) -> str:
    head: dict[str, Any] = {"alg": "EdDSA", "typ": typ}
    if kid:
        head["kid"] = kid
    return b64u(canonical_bytes(head))


def sign_compact(payload: dict, key: Ed25519PrivateKey, kid: str | None = None,
                 typ: str = "JWT") -> str:
    protected = _header(kid, typ)
    body = b64u(canonical_bytes(payload))
    signing_input = f"{protected}.{body}".encode("ascii")
    return f"{protected}.{body}.{b64u(key.sign(signing_input))}"


def verify_compact(token: str, key: Ed25519PublicKey) -> dict:
    try:
        protected, body, signature = token.split(".")
    except ValueError as exc:
        raise BadSignature("malformed compact JWS") from exc
    try:
        key.verify(b64u_decode(signature), f"{protected}.{body}".encode("ascii"))
    except InvalidSignature as exc:
        raise BadSignature("signature does not verify") from exc
    return json.loads(b64u_decode(body))


def peek(token: str) -> dict:
    """Read claims WITHOUT verifying. Only for routing/lookup, never for trust."""
    return json.loads(b64u_decode(token.split(".")[1]))


def sign_detached(payload: dict, key: Ed25519PrivateKey, kid: str | None = None,
                  typ: str = "JWS") -> str:
    """Returns `<protected>..<signature>` -- the payload is transported apart."""
    protected = _header(kid, typ)
    body = b64u(canonical_bytes(payload))
    return f"{protected}..{b64u(key.sign(f'{protected}.{body}'.encode('ascii')))}"


def verify_detached(detached: str, payload: dict, key: Ed25519PublicKey) -> None:
    try:
        protected, empty, signature = detached.split(".")
    except ValueError as exc:
        raise BadSignature("malformed detached JWS") from exc
    if empty:
        raise BadSignature("detached JWS must carry an empty payload segment")
    body = b64u(canonical_bytes(payload))
    try:
        key.verify(b64u_decode(signature), f"{protected}.{body}".encode("ascii"))
    except InvalidSignature as exc:
        raise BadSignature("signature does not match the payload received") from exc
