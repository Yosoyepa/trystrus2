"""M3 escalation API: signed approval evidence and fail-closed timeout."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from api.config import Settings
from api.services import escalations
from api.services.keys import KeyStore
from trustlib.jose import verify_compact
from trustlib.models import EscalationStatus, ReasonCode

pytestmark = pytest.mark.asyncio


async def test_approval_emits_a_verifiable_receipt_and_never_changes_purchase(
    session, tmp_path, monkeypatch
):
    store = KeyStore(Settings(secrets_dir=tmp_path, gcp_project=None))
    monkeypatch.setattr(escalations, "key_store", lambda: store)
    created = await escalations.create(
        session,
        purchase_id="pur_hil",
        mandate_id="mdt_hil",
        diff={"limit": "max_per_txn", "attempted": "300.00"},
    )

    resolved = await escalations.resolve(
        session,
        escalation_id=created.escalation_id,
        decision="APPROVE",
        approver="usr_marta",
        channel="web",
    )

    assert resolved.status is EscalationStatus.RESOLVED
    assert resolved.resolution and resolved.resolution.receipt_sig
    receipt = verify_compact(resolved.resolution.receipt_sig, store.issuer_key().key)
    assert receipt["escalation_id"] == created.escalation_id
    assert receipt["decision"] == "APPROVE"
    # The only emitted instruction is to re-run the gate; there is no purchase
    # mutation or reservation in this Dev 3 path.
    # `outbox.payload` is TEXT (the agent lane's table, shared verbatim), so
    # a raw SELECT gets the JSON string back, not a dict.
    events = (await session.execute(text("SELECT type, payload FROM outbox"))).all()
    assert [(row.type, json.loads(row.payload)["escalation_id"]) for row in events] == [
        ("escalation.resolved", created.escalation_id)
    ]


async def test_expired_escalation_is_lazily_denied_even_without_sweeper(session):
    created = await escalations.create(
        session,
        purchase_id="pur_late",
        mandate_id="mdt_late",
        diff={},
        timeout_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    listed = await escalations.list_escalations(session, mandate_id="mdt_late")

    assert listed[0].status is EscalationStatus.EXPIRED
    with pytest.raises(escalations.EscalationConflict) as late:
        await escalations.resolve(
            session,
            escalation_id=created.escalation_id,
            decision="APPROVE",
            approver="usr_marta",
            channel="telegram",
        )
    assert late.value.reason_code is ReasonCode.ESCALATION_TIMEOUT_DENIED
    events = (await session.execute(text("SELECT type FROM outbox"))).scalars().all()
    assert events == ["escalation.expired"]


async def test_human_rejection_compensates_without_an_approval_event(
    session, tmp_path, monkeypatch
):
    store = KeyStore(Settings(secrets_dir=tmp_path, gcp_project=None))
    monkeypatch.setattr(escalations, "key_store", lambda: store)
    created = await escalations.create(session, purchase_id="pur_no", mandate_id="mdt_no", diff={})

    resolved = await escalations.resolve(
        session,
        escalation_id=created.escalation_id,
        decision="REJECT",
        approver="usr_marta",
        channel="telegram",
    )

    assert resolved.status is EscalationStatus.RESOLVED
    assert resolved.resolution and resolved.resolution.decision == "REJECT"
    events = (await session.execute(text("SELECT type FROM outbox"))).scalars().all()
    assert events == ["escalation.expired"]
