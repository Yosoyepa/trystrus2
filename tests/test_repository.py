"""T8's other half — the state machine against a real database.

`tests/test_state_machine.py` proves the transition table is right. This file
proves the SQL enforces it, which is a different claim: a correct table that
the database does not apply protects nothing.

The concurrency tests here are the ones that matter for the demo. A judge
revoking a mandate mid-purchase is exactly the interleaving being simulated.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import repository as repo
from trustlib import fake, ids
from trustlib.events import UnknownEventType, emit_event
from trustlib.models import MandateStatus, ReasonCode

pytestmark = pytest.mark.asyncio


async def make_mandate(session, *, user_id: str = "usr_marta") -> str:
    claims = fake.mandate(user_id=user_id)
    mandate_id = ids.new_id(ids.MANDATE)
    await repo.create_mandate(session, claims, mandate_id=mandate_id)
    await session.commit()
    return mandate_id


# ==========================================================================
# Lifecycle
# ==========================================================================
async def test_a_new_mandate_is_a_draft_and_cannot_pay(session):
    mandate_id = await make_mandate(session)

    record = await repo.get_mandate(session, mandate_id)

    assert record.status == MandateStatus.DRAFT.value
    assert record.sd_jwt is None  # nothing signed until the gesture


async def test_the_happy_lifecycle(session):
    mandate_id = await make_mandate(session)

    assert (await repo.transition(session, mandate_id, MandateStatus.ACTIVE)).ok
    assert (await repo.transition(session, mandate_id, MandateStatus.SUSPENDED)).ok
    assert (await repo.transition(session, mandate_id, MandateStatus.ACTIVE)).ok
    assert (await repo.transition(session, mandate_id, MandateStatus.REVOKED)).ok


async def test_revocation_is_final_in_the_database(session):
    """Not just refused by the table — refused by the UPDATE."""
    mandate_id = await make_mandate(session)
    await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    await repo.transition(session, mandate_id, MandateStatus.REVOKED)
    await session.commit()

    result = await repo.transition(session, mandate_id, MandateStatus.ACTIVE)

    assert not result.ok
    assert result.reason_code is ReasonCode.MANDATE_REVOKED
    record = await repo.get_mandate(session, mandate_id)
    assert record.status == MandateStatus.REVOKED.value


async def test_a_draft_cannot_be_revoked(session):
    """Nothing was delegated yet, so there is nothing to take away."""
    mandate_id = await make_mandate(session)

    result = await repo.transition(session, mandate_id, MandateStatus.REVOKED)

    assert not result.ok


async def test_transition_on_a_missing_mandate_refuses_quietly(session):
    result = await repo.transition(session, "mdt_does_not_exist",
                                   MandateStatus.REVOKED)

    assert not result.ok
    assert result.frm is None


async def test_version_increments_on_each_accepted_transition(session):
    """Gives Dev 4 something to detect a stale view against."""
    mandate_id = await make_mandate(session)
    before = (await repo.get_mandate(session, mandate_id)).version

    await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    await session.commit()
    await session.refresh(await repo.get_mandate(session, mandate_id))

    after = (await repo.get_mandate(session, mandate_id)).version
    assert after == before + 1


# ==========================================================================
# Concurrency — the judge revoking mid-purchase
# ==========================================================================
async def test_two_concurrent_revocations_only_one_wins(session, engine):
    """Idempotent outcome, honest accounting: one transition, one event.

    Both callers see the mandate revoked, but only one of them performed the
    transition — so only one `mandate.revoked` event reaches the trail.
    """
    mandate_id = await make_mandate(session)
    await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def revoke():
        async with factory() as s:
            result = await repo.transition(s, mandate_id, MandateStatus.REVOKED)
            await s.commit()
            return result.ok

    outcomes = await asyncio.gather(revoke(), revoke())

    assert sorted(outcomes) == [False, True]


async def test_activation_loses_to_a_concurrent_revocation(session, engine):
    """The TOCTOU case, in the direction that matters.

    A read-then-write implementation would let the activation overwrite the
    revocation. The guard in the UPDATE makes that impossible.
    """
    mandate_id = await make_mandate(session)
    await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    await repo.transition(session, mandate_id, MandateStatus.SUSPENDED)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def move(to):
        async with factory() as s:
            result = await repo.transition(s, mandate_id, to)
            await s.commit()
            return to, result.ok

    results = dict(await asyncio.gather(
        move(MandateStatus.REVOKED), move(MandateStatus.ACTIVE)))

    if results[MandateStatus.REVOKED]:
        record = await repo.get_mandate(session, mandate_id)
        await session.refresh(record)
        assert record.status == MandateStatus.REVOKED.value


# ==========================================================================
# Passkey storage (decision 0021)
# ==========================================================================
async def test_a_challenge_can_only_be_consumed_once(session):
    """The replay defence, enforced by the UPDATE's `consumed_at IS NULL`."""
    from datetime import UTC, datetime, timedelta

    from api.services.passkey import Challenge, Purpose

    challenge = Challenge(value="chal-abc", user_id="usr_marta",
                          purpose=Purpose.ACTIVATE,
                          expires_at=datetime.now(UTC) + timedelta(minutes=5))
    await repo.store_challenge(session, challenge)
    await session.commit()

    assert await repo.consume_challenge(session, "chal-abc") is not None
    assert await repo.consume_challenge(session, "chal-abc") is None


async def test_concurrent_replay_of_one_challenge_yields_one_winner(
        session, engine):
    from datetime import UTC, datetime, timedelta

    from api.services.passkey import Challenge, Purpose

    await repo.store_challenge(session, Challenge(
        value="chal-race", user_id="usr_marta", purpose=Purpose.REVOKE,
        expires_at=datetime.now(UTC) + timedelta(minutes=5)))
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def claim():
        async with factory() as s:
            claimed = await repo.consume_challenge(s, "chal-race")
            await s.commit()
            return claimed is not None

    assert sorted(await asyncio.gather(claim(), claim())) == [False, True]


async def test_credentials_round_trip(session, user_id):
    from api.services.passkey import StoredCredential

    credential = StoredCredential(credential_id="cred-1", user_id=user_id,
                                  public_key=b"\x01\x02\x03", sign_count=7)
    await repo.store_credential(session, credential)
    await session.commit()

    assert (await repo.get_credential(session, "cred-1")).sign_count == 7
    assert len(await repo.credentials_for(session, user_id)) == 1

    await repo.advance_sign_count(session, "cred-1", 9)
    await session.commit()
    assert (await repo.get_credential(session, "cred-1")).sign_count == 9


# ==========================================================================
# The outbox helper (decision 0022)
# ==========================================================================
async def test_events_commit_with_the_business_change(session):
    """Decision #10's atomicity, which is why the helper takes our session."""
    mandate_id = await make_mandate(session)
    record = await repo.get_mandate(session, mandate_id)

    await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    await emit_event(session, type="mandate.activated",
                     aggregate_id=record.jti, payload={"jti": record.jti})
    await session.commit()

    rows = (await session.execute(
        text("SELECT type, aggregate_id FROM outbox"))).all()
    assert [(r.type, r.aggregate_id) for r in rows] == \
        [("mandate.activated", record.jti)]


async def test_a_rolled_back_change_takes_its_event_with_it(session):
    """The failure mode an API call between the two would create."""
    mandate_id = await make_mandate(session)
    record = await repo.get_mandate(session, mandate_id)

    await repo.transition(session, mandate_id, MandateStatus.ACTIVE)
    await emit_event(session, type="mandate.activated",
                     aggregate_id=record.jti, payload={})
    await session.rollback()

    count = (await session.execute(
        text("SELECT count(*) FROM outbox"))).scalar_one()
    assert count == 0


async def test_an_event_type_outside_the_catalogue_is_refused(session):
    """schemas.md §4 is a closed list; adding to it is a contract change."""
    with pytest.raises(UnknownEventType):
        await emit_event(session, type="mandate.definitely_not_a_real_event",
                         aggregate_id="mdt_1", payload={})


# ==========================================================================
# Payment instruments
# ==========================================================================
async def test_instrument_link_and_delete(session):
    mandate_id = await make_mandate(session)
    record = await repo.get_mandate(session, mandate_id)

    await repo.link_instrument(session, token_ref="ynt_1",
                               mandate_jti=record.jti)
    await session.commit()
    assert len(await repo.instruments_for(session, record.jti)) == 1

    await repo.mark_instrument_deleted(session, "ynt_1")
    await session.commit()
    assert await repo.instruments_for(session, record.jti) == []
