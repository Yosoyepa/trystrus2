"""Canonical JSON (RFC 8785 / JCS).

Every signature in Aval is computed over canonical bytes, never over whatever
`json.dumps` happened to produce. Without this, a signature made by the agent
(Dev 1) is not verifiable by the kernel (Dev 2) or the merchant (Dev 3) --
key order and number formatting differ per serializer.

We delegate to `rfc8785` rather than hand-rolling: the number serialization
rules (ECMAScript `Number::toString`) have edge cases that a "sorted keys plus
compact separators" implementation gets wrong.
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


def canonical_json(obj: Any) -> bytes:
    """Serialize `obj` to RFC 8785 canonical JSON bytes."""
    return rfc8785.dumps(obj)


def canonical_hash(obj: Any) -> bytes:
    """SHA-256 over the canonical form of `obj`.

    This is the passkey ceremony challenge (decision #3: the biometric gesture
    signs the exact permission, not a session) and the basis of the AP2
    `checkout_hash` binding.
    """
    return hashlib.sha256(canonical_json(obj)).digest()
