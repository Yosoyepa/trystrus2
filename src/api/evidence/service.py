"""EvidenceService: assemble the R-EVIDENCE pack for a purchase.

The case of use behind GET /purchases/{id}/evidence-pack (the HTTP endpoint
belongs to Dev 3). Fail-closed rule (D-1): a pack is assembled even when
pieces are missing or the chain fails, but then ``integrity == "failed"``
with explicit reasons — evidence is never presented as sound when it is not,
and failures are never hidden.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.api.canonical import canonical_json

from .models import (
    INTEGRITY_FAILED,
    INTEGRITY_OK,
    EvidencePack,
    pack_digest,
)
from .ports import (
    IntentReader,
    LedgerMirror,
    MandateClaimsReader,
    PurchaseReader,
    ReceiptReader,
    WitnessReader,
)


class EvidenceNotFoundError(LookupError):
    """Raised when the purchase id is unknown (Dev 3 maps it to 404)."""


class EvidenceService:
    """Aggregate mandate + intent + decision + receipt + ledger + witness."""

    def __init__(
        self,
        *,
        purchases: PurchaseReader,
        mandates: MandateClaimsReader,
        intents: IntentReader,
        receipts: ReceiptReader,
        ledger: LedgerMirror,
        witness: WitnessReader,
    ) -> None:
        self._purchases = purchases
        self._mandates = mandates
        self._intents = intents
        self._receipts = receipts
        self._ledger = ledger
        self._witness = witness

    def assemble(self, purchase_id: str, *, now: datetime) -> EvidencePack:
        purchase = self._purchases.get_purchase(purchase_id)
        if purchase is None:
            raise EvidenceNotFoundError(f"unknown purchase: {purchase_id}")

        mandate_jti = str(purchase.get("intent_jti", "") or "")
        if not mandate_jti:
            raise ValueError(f"purchase {purchase_id} has no intent_jti")

        failures: list[str] = []

        mandate_claims = self._mandates.get_claims(mandate_jti)
        if mandate_claims is None:
            failures.append("mandate-claims-missing")

        intent = self._intents.get_intent(mandate_jti)
        if intent is None:
            failures.append("intent-missing")

        decision = self._decision_snapshot(purchase)
        if decision is None:
            failures.append("decision-missing")

        receipt = self._receipts.get_receipt(purchase_id)

        events = self._ledger.events_for(mandate_jti)
        if not events:
            failures.append("ledger-slice-empty")

        chain = self._ledger.chain_verdict(mandate_jti)
        if not chain.ok:
            failures.append(f"chain-failed:{chain.reason}")

        checkpoint = self._witness.latest_checkpoint(mandate_jti)
        if checkpoint is None:
            failures.append("root-checkpoint-missing")

        pack_parts: dict[str, Any] = {
            "purchase_id": purchase_id,
            "mandate_jti": mandate_jti,
            "mandate_claims": mandate_claims,
            "intent": intent,
            "decision": decision,
            "receipt": receipt,
            "ledger_events": [dict(e) for e in events],
            "chain": {"ok": chain.ok, "first_bad_seq": chain.first_bad_seq},
            "root_checkpoint": checkpoint,
        }
        digest = pack_digest(pack_parts)

        return EvidencePack(
            purchase_id=purchase_id,
            mandate_jti=mandate_jti,
            integrity=INTEGRITY_FAILED if failures else INTEGRITY_OK,
            generated_at=now,
            digest=digest,
            mandate_claims=mandate_claims,
            intent=intent,
            decision=decision,
            receipt=receipt,
            ledger_events=tuple(dict(e) for e in events),
            chain=chain,
            root_checkpoint=checkpoint,
            failure_reasons=tuple(failures),
        )

    @staticmethod
    def _decision_snapshot(purchase: Mapping[str, Any]) -> dict[str, Any] | None:
        status = purchase.get("status")
        if not status:
            return None
        snapshot: dict[str, Any] = {"status": str(status)}
        for key in ("reason_code", "reservation_id", "escalation_id", "captured_at"):
            value = purchase.get(key)
            if value is not None:
                snapshot[key] = value
        return snapshot


def pack_canonical_bytes(pack: EvidencePack) -> bytes:
    """Canonical serialization of the pack envelope (for hashing/signing)."""

    return canonical_json(pack.to_dict()).encode("utf-8")


__all__ = [
    "EvidenceNotFoundError",
    "EvidenceService",
    "pack_canonical_bytes",
]
