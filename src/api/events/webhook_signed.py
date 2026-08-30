"""Signed webhook sink delivering canonical event payloads signed via RootSigner
(decisions #10, #15).
"""

from __future__ import annotations

import httpx

from src.api.audit.hashing import canonical_json
from src.api.audit.ports import RootSigner

from .ports import OutboxEvent


class SignedWebhookPoster:
    """HTTP webhook sink that signs event payloads with the evidence key (decision #15).

    Contract:
    - Body: Canonical JSON serialization of the OutboxEvent envelope.
    - Header: `X-Aval-Signature: ed25519=<hex_signature>` and `X-Aval-Key-ID: <key_id>`.
    - Fail-closed: non-2xx responses or network failures raise exceptions for outbox retry.
    """

    def __init__(
        self,
        target_url: str,
        signer: RootSigner,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.target_url = target_url
        self._signer = signer
        self._client = client
        self._timeout = timeout

    def handle(self, event: OutboxEvent) -> None:
        """Sign and deliver event over HTTP."""
        envelope = event.to_dict()
        canonical_str = canonical_json(envelope)
        canonical_bytes = canonical_str.encode("utf-8")

        sig_bytes = self._signer.sign(canonical_bytes)
        sig_hex = sig_bytes.hex()

        headers = {
            "Content-Type": "application/json",
            "X-Aval-Signature": f"ed25519={sig_hex}",
            "X-Aval-Key-ID": self._signer.key_id,
            "X-Aval-Event-ID": event.event_id,
            "X-Aval-Event-Type": event.type,
        }

        if self._client is not None:
            resp = self._client.post(
                self.target_url,
                content=canonical_bytes,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        else:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    self.target_url,
                    content=canonical_bytes,
                    headers=headers,
                )
                resp.raise_for_status()


__all__ = ["SignedWebhookPoster"]
