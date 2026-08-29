"""The Protocols of `aval/contracts/schemas.md` §3.

**Copied from the contract, not designed here.** Dev 1, 2 and 4 build against
these signatures; changing one is a contract change (PR + decision record +
mock + fixtures in the same commit, PLAN-PARALELO §6.2).

Ownership, per decision #19:
* `PolicyGate`, `Ledger` -- Dev 2
* `PaymentRail`, `MandateRegistry` -- Dev 3
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from .models import (
    Decision,
    DisputeRef,
    IssuedMandate,
    MandateClaims,
    MandateClaimsInput,
    PurchaseIntent,
    Receipt,
    SetupToken,
    SpendView,
    WebhookEvent,
)


@runtime_checkable
class PolicyGate(Protocol):
    """[Dev 2] Deterministic, no I/O, tested by T1/T10.

    Same input, same answer, every time -- a decision that can vary between
    two identical requests is not a control (decision #1).
    """

    def evaluate(self, mandate: MandateClaims, intent: PurchaseIntent,
                 spend: SpendView, now: datetime) -> Decision: ...


@runtime_checkable
class PaymentRail(Protocol):
    """[Dev 3] The only route to money, behind one seam.

    This Protocol is what keeps the rail swappable: the Yuno-style simulated
    orchestrator, a real orchestrator, or x402 all satisfy it without any
    caller changing (ADR-007's rail-agnostic thesis).
    """

    def create_setup_token(self, mandate_id: str) -> SetupToken:
        """Begin enrollment -> approve_url for the human's one-time approval."""
        ...

    def exchange_payment_token(self, setup_token_id: str) -> str:
        """Approved setup token -> vaulted payment token id."""
        ...

    def delete_payment_token(self, token_id: str) -> None:
        """Rail-side kill switch. Idempotent (decision #4)."""
        ...

    def capture(self, *, token_id: str, amount: Decimal, currency: str,
                idempotency_key: str, intent_ref: str) -> Receipt:
        """Charge the vaulted instrument. Same key -> same result, no double charge."""
        ...

    def open_dispute(self, capture_id: str, reason: str = "UNAUTHORISED") -> DisputeRef: ...

    def verify_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None:
        """Return the event only if the signature checks out; None otherwise."""
        ...


@runtime_checkable
class MandateRegistry(Protocol):
    """[Dev 3] SD-JWT issuance and verification."""

    def issue(self, claims: MandateClaimsInput) -> IssuedMandate: ...

    def verify(self, sd_jwt: str, *, nonce: str, aud: str) -> MandateClaims:
        """Verify issuer signature and, when presented, the key binding."""
        ...

    def jwks(self) -> dict[str, Any]: ...


@runtime_checkable
class Ledger(Protocol):
    """[Dev 2] Append-only, hash-chained."""

    def append(self, type: str, mandate_id: str, payload: dict) -> Any: ...

    def verify_chain(self) -> Any: ...

    def sign_root(self) -> Any: ...
