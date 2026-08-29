"""Ed25519 key handling (C2, C10).

Keys live as PEM under var/keys/.  In production the issuer key is a Secret
Manager secret and evidence roots are signed by KMS -- the interface is the
same, only the loader changes.
"""
from __future__ import annotations
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..config import VAR_DIR

KEY_DIR = VAR_DIR / "keys"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_or_create(name: str) -> Ed25519PrivateKey:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    path = KEY_DIR / f"{name}.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    return key


def public_jwk(key: Ed25519PrivateKey | Ed25519PublicKey, kid: str | None = None) -> dict:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": b64u(raw)}
    if kid:
        jwk["kid"] = kid
    return jwk


def jwk_to_public(jwk: dict) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError("unsupported jwk")
    return Ed25519PublicKey.from_public_bytes(b64u_decode(jwk["x"]))
