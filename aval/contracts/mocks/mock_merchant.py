"""Fixture-driven merchant checkout double for pre-M2 consumers.

This is deliberately small: its job is to enforce the one contract property
frontends and the agent need before the real merchant is deployed — a refused
verify decision cannot reach a payment client.  It is not an approve-all fake.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from trustlib.models import Decision, DecisionOutcome, ReasonCode

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class CaptureClient(Protocol):
    async def capture(self, **kwargs): ...


def offers() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "offers.json").read_text())


def get_offer(offer_id: str) -> dict[str, Any] | None:
    return next((offer for offer in offers() if offer["offer_id"] == offer_id), None)


async def charge_after_verify(*, decision: Decision, rail: CaptureClient,
                              capture_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Mirror `/checkout/charge`: only APPROVED may call the rail spy/client."""
    if decision.decision is not DecisionOutcome.APPROVED:
        return {
            "status_code": 402,
            "reason_code": (decision.reason_code or ReasonCode.RAIL_ERROR).value,
        }
    receipt = await rail.capture(**capture_kwargs)
    return {"status_code": 200, "receipt": receipt}
