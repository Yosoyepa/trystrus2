"""Key handling and JWS signing for Aval.

Two curves, deliberately:

* **Ed25519 (EdDSA)** for the mandate SD-JWT (issuer), the purchase intent
  (agent) and approval receipts. Decisions #9 and #15.
* **P-256 (ES256)** for the merchant's AP2 Checkout JWT. Not a style choice --
  the AP2 specification forbids Ed25519 there:

      "To prevent rainbow table attacks, the Checkout JWT MUST be signed using
      a digital signature scheme (e.g., ECDSA) and not a deterministic
      signature (e.g., Ed25519)."

  The checkout is hash-bound, and a deterministic signature over a
  low-entropy checkout would be precomputable.

Detached JWS (RFC 7515 App. F) is used for the purchase intent: the payload
travels beside the signature as canonical JSON, so both sides sign exactly the
bytes defined by `canonical_json` rather than a base64 re-encoding.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from jwcrypto import jwk, jws

Alg = Literal["EdDSA", "ES256"]


# --------------------------------------------------------------------------
# base64url
# --------------------------------------------------------------------------
def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


# --------------------------------------------------------------------------
# key generation / conversion
# --------------------------------------------------------------------------
def generate_ed25519() -> jwk.JWK:
    """Fresh Ed25519 key as a JWK (private)."""
    return jwk.JWK.generate(kty="OKP", crv="Ed25519")


def generate_p256() -> jwk.JWK:
    """Fresh P-256 key as a JWK (private) -- for ES256 Checkout JWTs."""
    return jwk.JWK.generate(kty="EC", crv="P-256")


def key_from_pem(pem: bytes, *, password: bytes | None = None) -> jwk.JWK:
    """Load a private key from PEM.

    PEM is the storage format because Secret Manager holds text and the
    SD-JWT library wants local key material (decision #15).
    """
    return jwk.JWK.from_pem(pem, password=password)


def key_to_pem(key: jwk.JWK, *, private: bool = True) -> bytes:
    return key.export_to_pem(private_key=private, password=None)


def public_jwk(key: jwk.JWK, *, kid: str | None = None) -> dict[str, Any]:
    """Public half of `key` as a plain dict, ready for a JWK Set."""
    pub = json.loads(key.export_public())
    if kid:
        pub["kid"] = kid
    return pub


def jwk_from_dict(data: dict[str, Any]) -> jwk.JWK:
    return jwk.JWK(**data)


def alg_for(key: jwk.JWK) -> Alg:
    """The signing algorithm this key must use."""
    kty = key.get("kty")
    if kty == "OKP":
        return "EdDSA"
    if kty == "EC":
        return "ES256"
    raise ValueError(f"unsupported key type for Aval: {kty!r}")


def generate_pem_pair(curve: Literal["Ed25519", "P-256"]) -> tuple[bytes, dict[str, Any]]:
    """Return `(private_pem, public_jwk_dict)` -- used to seed fixtures and secrets."""
    if curve == "Ed25519":
        private = ed25519.Ed25519PrivateKey.generate()
    else:
        private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, public_jwk(key_from_pem(pem))


# --------------------------------------------------------------------------
# compact JWS (Checkout JWT, approval receipts)
# --------------------------------------------------------------------------
def sign_compact(
    payload: dict[str, Any], key: jwk.JWK, *, kid: str | None = None, typ: str | None = None
) -> str:
    """Sign `payload` as a compact JWS. Algorithm is derived from the key."""
    from .canonical import canonical_json

    header: dict[str, Any] = {"alg": alg_for(key)}
    if kid:
        header["kid"] = kid
    if typ:
        header["typ"] = typ

    token = jws.JWS(canonical_json(payload))
    token.add_signature(key, alg=header["alg"], protected=json.dumps(header))
    return token.serialize(compact=True)


def verify_compact(token: str, key: jwk.JWK) -> dict[str, Any]:
    """Verify a compact JWS and return its payload.

    Raises `jwcrypto.jws.InvalidJWSSignature` when the signature does not
    check out -- callers translate that into a ReasonCode.
    """
    verifier = jws.JWS()
    verifier.deserialize(token, key=key)
    return json.loads(verifier.payload)


def peek_header(token: str) -> dict[str, Any]:
    """Read the protected header without verifying -- to pick a key by `kid`."""
    return json.loads(b64u_decode(token.split(".", 1)[0]))


# --------------------------------------------------------------------------
# detached JWS (purchase intent -- schemas.md §2)
# --------------------------------------------------------------------------
def sign_detached(payload: dict[str, Any], key: jwk.JWK, *, kid: str | None = None) -> str:
    """Detached JWS over the canonical form of `payload`.

    The returned token has an empty payload segment (`header..signature`);
    the payload travels separately and is re-canonicalized by the verifier.
    """
    token = sign_compact(payload, key, kid=kid)
    header, _, signature = token.split(".")
    return f"{header}..{signature}"


def verify_detached(token: str, payload: dict[str, Any], key: jwk.JWK) -> dict[str, Any]:
    """Verify a detached JWS against the canonical form of `payload`."""
    from .canonical import canonical_json

    header, _, signature = token.split(".")
    reattached = f"{header}.{b64u_encode(canonical_json(payload))}.{signature}"
    return verify_compact(reattached, key)
