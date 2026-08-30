"""Google Cloud KMS Ed25519 signer for non-exportable evidence roots (decision #15)."""

from __future__ import annotations

import os
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization


class KMSRootSigner:
    """Cloud KMS signer using EC_SIGN_ED25519.

    The private key never leaves Cloud KMS hardware/HSM.
    Asymmetric signing calls `asymmetricSign` and verification fetches
    the public key to verify locally with cryptography.
    """

    def __init__(self, key_resource_name: str | None = None) -> None:
        self._key_name = key_resource_name or os.environ.get("AVAL_KMS_KEY_RESOURCE", "")
        if not self._key_name:
            raise ValueError("Cloud KMS key resource name required (set AVAL_KMS_KEY_RESOURCE)")
        self._client: Any = None
        self._cached_public_key: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import kms

                self._client = kms.KeyManagementServiceClient()
            except ImportError as exc:
                raise RuntimeError(
                    "google-cloud-kms package is required to use KMSRootSigner"
                ) from exc
        return self._client

    @property
    def key_id(self) -> str:
        return self._key_name

    def sign(self, data: bytes) -> bytes:
        """Sign payload using KMS asymmetricSign."""
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data to sign must be bytes")
        client = self._get_client()
        try:
            response = client.asymmetric_sign(request={"name": self._key_name, "data": bytes(data)})
            return bytes(response.signature)
        except Exception as exc:
            raise RuntimeError(
                f"Cloud KMS asymmetricSign failed for {self._key_name}: {exc}"
            ) from exc

    def _get_public_key(self) -> Any:
        if self._cached_public_key is None:
            client = self._get_client()
            try:
                response = client.get_public_key(request={"name": self._key_name})
                pem_data = response.pem.encode("utf-8")
                self._cached_public_key = serialization.load_pem_public_key(pem_data)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to fetch public key from KMS for {self._key_name}: {exc}"
                ) from exc
        return self._cached_public_key

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify signature against Cloud KMS public key."""
        if not isinstance(data, (bytes, bytearray)) or not isinstance(
            signature, (bytes, bytearray)
        ):
            return False
        try:
            pub_key = self._get_public_key()
            pub_key.verify(bytes(signature), bytes(data))
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False
        except Exception:
            # Fail closed on unexpected errors during verification
            return False


__all__ = ["KMSRootSigner"]
