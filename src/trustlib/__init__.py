"""trustlib -- the shared surface for every Aval workstream.

Community property (PLAN-PARALELO §4). Pydantic models of the frozen
contracts, canonical JSON, JOSE helpers, SD-JWT, the AP2 conformance layer,
the Protocols of schemas.md §3, and `fake.*` generators so each workstream
can test without the others.
"""

from . import ap2, fake, ids, interfaces, jose, sdjwt
from .canonical import canonical_hash, canonical_json
from .interfaces import Ledger, MandateRegistry, PaymentRail, PolicyGate
from .models import (
    Decision,
    DecisionOutcome,
    DisputeRef,
    Escalation,
    EscalationResolution,
    EscalationStatus,
    EventEnvelope,
    IssuedMandate,
    JWKSet,
    MandateClaims,
    MandateClaimsInput,
    MandateLimits,
    MandateScope,
    MandateStatus,
    MandateValidity,
    MaxTxn,
    Offer,
    Period,
    PurchaseIntent,
    PurchaseStatusValue,
    ReasonCode,
    Receipt,
    Rejection,
    SetupToken,
    SpendView,
    WebhookEvent,
)

__all__ = [
    "ap2", "fake", "ids", "interfaces", "jose", "sdjwt",
    "canonical_json", "canonical_hash",
    "PolicyGate", "PaymentRail", "MandateRegistry", "Ledger",
    "ReasonCode", "MandateStatus", "DecisionOutcome", "PurchaseStatusValue",
    "EscalationStatus", "Period",
    "MandateClaims", "MandateClaimsInput", "MandateLimits", "MandateScope",
    "MandateValidity", "MaxTxn", "IssuedMandate",
    "PurchaseIntent", "Offer", "Decision", "SpendView",
    "SetupToken", "Receipt", "DisputeRef", "WebhookEvent",
    "Escalation", "EscalationResolution", "EventEnvelope", "JWKSet",
    "Rejection",
]
