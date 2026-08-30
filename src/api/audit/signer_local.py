"""Local Ed25519 signer for development, testing, and sandbox environments."""

from __future__ import annotations

import os
from typing import Self

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


class LocalEd25519Signer:
    """Local Ed25519 signer implementing RootSigner port."""

    def __init__(
        self,
        private_key: ed25519.Ed25519PrivateKey | None = None,
        key_id: str = "local-ed25519-dev",
    ) -> None:
        if private_key is None:
            pem_env = os.environ.get("AVAL_LOCAL_SIGNER_PEM")
            if pem_env:
                if os.path.exists(pem_env):
                    with open(pem_env, "rb") as f:
                        pem_data = f.read()
                else:
                    pem_data = pem_env.encode("utf-8")
                loaded = serialization.load_pem_private_key(pem_data, password=None)
                if not isinstance(loaded, ed25519.Ed25519PrivateKey):
                    raise ValueError("PEM key is not an Ed25519 private key")
                self._private_key = loaded
            else:
                self._private_key = ed25519.Ed25519PrivateKey.generate()
        else:
            self._private_key = private_key
        self._key_id = key_id

    @classmethod
    def generate(cls, key_id: str = "local-ed25519-dev") -> Self:
        """Factory creating a freshly generated keypair."""
        return cls(private_key=ed25519.Ed25519PrivateKey.generate(), key_id=key_id)

    @classmethod
    def from_pem(cls, pem_bytes: bytes, key_id: str = "local-ed25519-pem") -> Self:
        """Factory creating signer from raw PEM bytes."""
        loaded = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(loaded, ed25519.Ed25519PrivateKey):
            raise ValueError("PEM key is not an Ed25519 private key")
        return cls(private_key=loaded, key_id=key_id)

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def export_private_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def sign(self, data: bytes) -> bytes:
        """Sign binary payload using Ed25519."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data to sign must be bytes")
        return self._private_key.sign(bytes(data))

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify Ed25519 signature against public key."""
        if (
            not isinstance(data, (bytes, bytearray))
            or not isinstance(signature, (bytes, bytearray))
        ):
            return False
        try:
            self._private_key.public_key().verify(bytes(signature), bytes(data))
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


__all__ = ["LocalEd25519Signer"]
