"""Evidence pack models (R-EVIDENCE, D-1 of the phase evolution plan)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.api.canonical import sha256_hex

INTEGRITY_OK = "ok"
INTEGRITY_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChainVerdict:
    """Outcome of verifying the ledger slice that backs a purchase."""

    ok: bool
    first_bad_seq: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.ok and not self.reason:
            raise ValueError("a failed chain verdict must carry a reason")
        if self.ok and self.first_bad_seq is not None:
            raise ValueError("an ok chain verdict cannot carry first_bad_seq")


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """The pack behind GET /purchases/{id}/evidence-pack (api.yaml, R-EVIDENCE).

    Fail-closed by construction: any missing piece or failed chain verdict
    yields ``integrity == "failed"`` with the reason preserved — the pack is
    never silently presented as sound, and it never omits the failure.
    """

    purchase_id: str
    mandate_jti: str
    integrity: str
    generated_at: datetime
    digest: str
    mandate_claims: Mapping[str, Any] | None = None
    intent: Mapping[str, Any] | None = None
    decision: Mapping[str, Any] | None = None
    receipt: Mapping[str, Any] | None = None
    ledger_events: tuple[Mapping[str, Any], ...] = ()
    chain: ChainVerdict | None = None
    root_checkpoint: Mapping[str, Any] | None = None
    failure_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.purchase_id or not self.mandate_jti:
            raise ValueError("evidence packs require purchase_id and mandate_jti")
        if self.integrity not in (INTEGRITY_OK, INTEGRITY_FAILED):
            raise ValueError(f"unknown integrity status: {self.integrity}")
        if self.integrity == INTEGRITY_FAILED and not self.failure_reasons:
            raise ValueError("a failed pack must list failure_reasons")
        if self.integrity == INTEGRITY_OK and self.failure_reasons:
            raise ValueError("an ok pack cannot carry failure_reasons")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        """Envelope handed to Dev 3 for the HTTP response."""

        return {
            "purchase_id": self.purchase_id,
            "mandate_jti": self.mandate_jti,
            "integrity": self.integrity,
            "generated_at": self.generated_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "digest": self.digest,
            "mandate_claims": self.mandate_claims,
            "intent": self.intent,
            "decision": self.decision,
            "receipt": self.receipt,
            "ledger_events": list(self.ledger_events),
            "chain": (
                {
                    "ok": self.chain.ok,
                    "first_bad_seq": self.chain.first_bad_seq,
                    "reason": self.chain.reason,
                }
                if self.chain is not None
                else None
            ),
            "root_checkpoint": self.root_checkpoint,
            "failure_reasons": list(self.failure_reasons),
        }


def pack_digest(parts: Mapping[str, Any]) -> str:
    """Stable digest over the pack contents (canonical, evidence-grade)."""

    return sha256_hex(parts)


__all__ = [
    "ChainVerdict",
    "EvidencePack",
    "INTEGRITY_FAILED",
    "INTEGRITY_OK",
    "pack_digest",
]
