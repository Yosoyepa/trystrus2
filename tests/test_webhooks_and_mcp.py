"""T14 signed webhooks and the deliberately narrow merchant MCP boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.background import BackgroundTasks

import merchant.main as merchant_main
import merchant.mcp_server as mcp_module
import yuno_sim.main as yuno_main
from merchant.config import Settings as MerchantSettings
from merchant.mcp_server import mcp, request_purchase
from merchant.webhooks import WebhookVerificationError, YunoWebhookVerifier
from yuno_sim.config import Settings as YunoSettings
from yuno_sim.payments import Settlement
from yuno_sim.schemas import CaptureRequest
from yuno_sim.webhooks_out import WebhookSigner

pytestmark = pytest.mark.asyncio


async def test_signed_yuno_webhook_is_accepted_and_tampering_is_401(tmp_path):
    signer = WebhookSigner(config=YunoSettings(secrets_dir=tmp_path))
    event = signer.event(type="payment.captured", payload={"capture_id": "ynp_1"})
    body, headers = signer.serialize(event)
    verifier = YunoWebhookVerifier(
        config=MerchantSettings(secrets_dir=tmp_path),
        keys={"yuno-webhook-v1": signer._signing_key()},
    )

    assert (await verifier.verify(headers=headers, body=body)).event_id == event.event_id
    with pytest.raises(WebhookVerificationError):
        await verifier.verify(headers=headers, body=body.replace(b"ynp_1", b"ynp_9"))

    previous = merchant_main._webhook_verifier
    merchant_main._webhook_verifier = verifier
    try:
        async with AsyncClient(
            transport=ASGITransport(merchant_main.app), base_url="http://merchant"
        ) as client:
            accepted = await client.post("/webhooks/yuno", content=body, headers=headers)
            rejected = await client.post(
                "/webhooks/yuno", content=body.replace(b"ynp_1", b"ynp_9"), headers=headers
            )
    finally:
        merchant_main._webhook_verifier = previous

    assert accepted.status_code == 200
    assert rejected.status_code == 401
    assert rejected.json()["reason_code"] == "INVALID_SIGNATURE"


async def test_successful_rail_capture_schedules_a_signed_captured_event(tmp_path, monkeypatch):
    """`payment.captured` is emitted by the real HTTP handler, not just the
    serializer in isolation.  The task remains after the response path so
    delivery cannot become a prerequisite for settlement.
    """
    signer = WebhookSigner(config=YunoSettings(secrets_dir=tmp_path))
    delivered = []

    async def fake_capture(*_args, **_kwargs):
        return Settlement(
            payment_id="ynp_webhook",
            amount=Decimal("130.00"),
            currency="USD",
            mandate_jti="mdt_webhook",
            captured_at=datetime.now(UTC),
        )

    async def fake_deliver(event):
        delivered.append(event)
        return True

    monkeypatch.setattr(yuno_main, "capture", fake_capture)
    monkeypatch.setattr(yuno_main, "deliver", fake_deliver)
    monkeypatch.setattr(yuno_main, "signer", lambda: signer)

    tasks = BackgroundTasks()
    receipt = await yuno_main.create_payment(
        background_tasks=tasks,
        body=CaptureRequest(
            token_id="ynt_webhook",
            amount="130.00",
            intent_ref="int_webhook",
            purchase_id="pur_webhook",
            mandate_sd_jwt="signed",
            checkout_jwt="cart",
        ),
        idempotency_key="idem_webhook",
        session=object(),
    )
    await tasks()

    assert receipt.capture_id == "ynp_webhook"
    assert [(event.type, event.payload["capture_id"]) for event in delivered] == [
        ("payment.captured", "ynp_webhook")
    ]


class PurchaseSpy:
    def __init__(self):
        self.calls: list[dict] = []

    async def submit(self, **kwargs):
        self.calls.append(kwargs)
        return {"purchase_id": "pur_from_kernel"}


async def test_mcp_has_exactly_three_tools_and_never_accepts_an_amount(session):
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {"search_offers", "get_offer", "request_purchase"}

    spy = PurchaseSpy()
    previous = mcp_module._purchase_client
    mcp_module._purchase_client = spy
    try:
        result = await request_purchase("ofr_COR_130", "mdt_agent")
    finally:
        mcp_module._purchase_client = previous

    assert result == {"status": "submitted", "purchase_id": "pur_from_kernel"}
    assert spy.calls == [{"offer_id": "ofr_COR_130", "mandate_jti": "mdt_agent"}]
