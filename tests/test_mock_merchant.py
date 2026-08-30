"""The community merchant mock must preserve the real checkout's refusal path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trustlib.models import Decision, DecisionOutcome, ReasonCode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "aval" / "contracts" / "mocks"))
import mock_merchant  # noqa: E402


class RailSpy:
    def __init__(self):
        self.calls = 0

    async def capture(self, **kwargs):
        self.calls += 1
        return {"capture_id": "ynp_mock", **kwargs}


@pytest.mark.asyncio
async def test_mock_merchant_does_not_capture_when_verify_refuses():
    rail = RailSpy()
    response = await mock_merchant.charge_after_verify(
        decision=Decision(
            decision=DecisionOutcome.REJECTED, reason_code=ReasonCode.MANDATE_REVOKED
        ),
        rail=rail,
        capture_kwargs={"amount": "130.00"},
    )

    assert response == {"status_code": 402, "reason_code": "MANDATE_REVOKED"}
    assert rail.calls == 0


@pytest.mark.asyncio
async def test_mock_merchant_captures_only_approved_decisions():
    rail = RailSpy()
    response = await mock_merchant.charge_after_verify(
        decision=Decision(decision=DecisionOutcome.APPROVED),
        rail=rail,
        capture_kwargs={"amount": "130.00"},
    )

    assert response["status_code"] == 200
    assert rail.calls == 1
