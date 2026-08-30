"""T8 -- mandate state machine (PLAN.md §10).

"Invalid transitions -> 0 rows + a rejection event."

Table-driven over *every* (from, to) pair, not a hand-picked sample. The
interesting failures in a state machine are the pairs nobody thought to write
a test for -- reactivating a revoked mandate, suspending an expired one.
"""

from __future__ import annotations

import itertools

import pytest

from api.services import state_machine as sm
from trustlib.models import MandateStatus, ReasonCode

ALL = list(MandateStatus)

# The full specification of PLAN.md §7, written once, independently of the
# implementation's table. If the two ever disagree, this file says so.
LEGAL: set[tuple[MandateStatus, MandateStatus]] = {
    (MandateStatus.DRAFT, MandateStatus.ACTIVE),
    (MandateStatus.ACTIVE, MandateStatus.SUSPENDED),
    (MandateStatus.SUSPENDED, MandateStatus.ACTIVE),
    (MandateStatus.ACTIVE, MandateStatus.REVOKED),
    (MandateStatus.SUSPENDED, MandateStatus.REVOKED),
    (MandateStatus.ACTIVE, MandateStatus.EXPIRED),
    (MandateStatus.SUSPENDED, MandateStatus.EXPIRED),
    (MandateStatus.ACTIVE, MandateStatus.EXHAUSTED),
}


@pytest.mark.parametrize(("frm", "to"), list(itertools.product(ALL, ALL)))
def test_every_pair_matches_the_specification(frm, to):
    """All 36 pairs, checked against the spec rather than the code."""
    assert sm.is_allowed(frm, to) is ((frm, to) in LEGAL), f"{frm.value} -> {to.value}"


# ==========================================================================
# Terminal states are terminal
# ==========================================================================
@pytest.mark.parametrize(
    "terminal",
    [
        MandateStatus.REVOKED,
        MandateStatus.EXPIRED,
        MandateStatus.EXHAUSTED,
    ],
)
@pytest.mark.parametrize("target", ALL)
def test_nothing_leaves_a_terminal_state(terminal, target):
    """A buyer who revoked has revoked -- there is no path back."""
    assert not sm.is_allowed(terminal, target)


def test_revoked_cannot_be_reactivated():
    """Called out separately because it is the one an attacker would try."""
    assert not sm.is_allowed(MandateStatus.REVOKED, MandateStatus.ACTIVE)

    with pytest.raises(sm.InvalidTransition) as caught:
        sm.check(MandateStatus.REVOKED, MandateStatus.ACTIVE)
    assert caught.value.reason_code is ReasonCode.MANDATE_REVOKED


def test_draft_is_never_a_destination():
    """You cannot walk a live mandate back to unsigned."""
    for frm in ALL:
        assert not sm.is_allowed(frm, MandateStatus.DRAFT)


def test_a_draft_mandate_cannot_be_revoked_or_suspended():
    """Nothing was delegated yet, so there is nothing to take away."""
    assert not sm.is_allowed(MandateStatus.DRAFT, MandateStatus.REVOKED)
    assert not sm.is_allowed(MandateStatus.DRAFT, MandateStatus.SUSPENDED)


# ==========================================================================
# The legal paths
# ==========================================================================
def test_the_activation_path():
    """draft -> active is the passkey ceremony completing (decision #3)."""
    assert sm.is_allowed(MandateStatus.DRAFT, MandateStatus.ACTIVE)


def test_suspension_is_reversible_and_revocation_is_not():
    assert sm.is_allowed(MandateStatus.ACTIVE, MandateStatus.SUSPENDED)
    assert sm.is_allowed(MandateStatus.SUSPENDED, MandateStatus.ACTIVE)
    assert sm.is_allowed(MandateStatus.ACTIVE, MandateStatus.REVOKED)
    assert not sm.is_allowed(MandateStatus.REVOKED, MandateStatus.SUSPENDED)


def test_a_suspended_mandate_can_still_be_revoked():
    """Marta must be able to revoke without reactivating first."""
    assert sm.is_allowed(MandateStatus.SUSPENDED, MandateStatus.REVOKED)


def test_exhaustion_only_happens_to_an_active_mandate():
    """The counter is consumed by spending, which a suspended mandate cannot do."""
    assert sm.is_allowed(MandateStatus.ACTIVE, MandateStatus.EXHAUSTED)
    assert not sm.is_allowed(MandateStatus.SUSPENDED, MandateStatus.EXHAUSTED)


# ==========================================================================
# The SQL guard and the in-memory table must not drift
# ==========================================================================
@pytest.mark.parametrize("to", ALL)
def test_sql_guard_lists_exactly_the_legal_sources(to):
    """`WHERE status = ANY(sources_for(to))` is the whole enforcement.

    If this list ever disagrees with `is_allowed`, the database would permit
    a transition the code believes it forbids -- which is the only kind of
    state machine bug that matters.
    """
    expected = sorted(f.value for f in ALL if sm.is_allowed(f, to))
    assert sm.sources_for(to) == expected


def test_a_transition_with_no_legal_source_produces_an_empty_guard():
    """An empty guard means the UPDATE matches nothing. Fail closed."""
    assert sm.sources_for(MandateStatus.DRAFT) == []


# ==========================================================================
# Refusals carry a reason the buyer can read
# ==========================================================================
@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (MandateStatus.REVOKED, ReasonCode.MANDATE_REVOKED),
        (MandateStatus.EXPIRED, ReasonCode.MANDATE_EXPIRED),
        (MandateStatus.EXHAUSTED, ReasonCode.MANDATE_EXHAUSTED),
        (MandateStatus.SUSPENDED, ReasonCode.MANDATE_SUSPENDED),
    ],
)
def test_refusal_names_the_state_not_the_mechanism(current, expected):
    """ "Your mandate was revoked" beats "invalid transition" in three consoles."""
    assert sm.refusal_reason(current, MandateStatus.ACTIVE) is expected


def test_an_unrecognised_state_denies():
    """Fail closed: a state we cannot reason about does not get the benefit."""
    assert (
        sm.refusal_reason(MandateStatus.DRAFT, MandateStatus.REVOKED)
        is ReasonCode.MANDATE_SUSPENDED
    )


def test_check_passes_silently_on_a_legal_transition():
    assert sm.check(MandateStatus.DRAFT, MandateStatus.ACTIVE) is None


# ==========================================================================
# Every accepted transition is auditable
# ==========================================================================
@pytest.mark.parametrize(("frm", "to"), sorted(LEGAL, key=lambda p: (p[0], p[1])))
def test_every_legal_transition_emits_a_named_event(frm, to):
    """No silent state change: the trail names what happened (schemas.md §4)."""
    assert sm.TRANSITION_EVENTS[to].startswith("mandate.")
