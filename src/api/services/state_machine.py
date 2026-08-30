"""Mandate state machine (PLAN.md §7).

    draft -> active -> (suspended <-> active) -> {revoked | expired | exhausted}

The last three are terminal: nothing leaves them, including back to active.
A buyer who revokes has revoked.

Two rules make this trustworthy rather than merely correct:

1. **Every transition is a guarded UPDATE**, never read-then-write. The guard
   carries the allowed source states, so a concurrent revocation between our
   read and our write loses -- zero rows updated -- instead of being
   overwritten. This is the same mechanism that closes TOCTOU on the charging
   path (decision #4).

2. **Zero rows updated is a refusal, not an exception to swallow.** It returns
   a typed failure with a ReasonCode and an audit event, so a rejected
   transition is as visible in the trail as an accepted one.
"""

from __future__ import annotations

from dataclasses import dataclass

from trustlib.models import MandateStatus, ReasonCode

# Which source states each target admits. Anything not listed is refused --
# the table is an allowlist, so a new status added later fails closed until
# someone decides where it may come from.
ALLOWED_TRANSITIONS: dict[MandateStatus, frozenset[MandateStatus]] = {
    MandateStatus.ACTIVE: frozenset({MandateStatus.DRAFT, MandateStatus.SUSPENDED}),
    MandateStatus.SUSPENDED: frozenset({MandateStatus.ACTIVE}),
    MandateStatus.REVOKED: frozenset({MandateStatus.ACTIVE, MandateStatus.SUSPENDED}),
    MandateStatus.EXPIRED: frozenset({MandateStatus.ACTIVE, MandateStatus.SUSPENDED}),
    MandateStatus.EXHAUSTED: frozenset({MandateStatus.ACTIVE}),
    # `draft` is where a mandate is created, never a destination.
    MandateStatus.DRAFT: frozenset(),
}

# The reason a refused transition reports, when the mandate is already in a
# state that explains itself better than "invalid transition" would.
TERMINAL_REASONS: dict[MandateStatus, ReasonCode] = {
    MandateStatus.REVOKED: ReasonCode.MANDATE_REVOKED,
    MandateStatus.EXPIRED: ReasonCode.MANDATE_EXPIRED,
    MandateStatus.EXHAUSTED: ReasonCode.MANDATE_EXHAUSTED,
    MandateStatus.SUSPENDED: ReasonCode.MANDATE_SUSPENDED,
}

# Event emitted on each successful transition (schemas.md §4).
TRANSITION_EVENTS: dict[MandateStatus, str] = {
    MandateStatus.ACTIVE: "mandate.activated",
    MandateStatus.SUSPENDED: "mandate.suspended",
    MandateStatus.REVOKED: "mandate.revoked",
    MandateStatus.EXPIRED: "mandate.expired",
    MandateStatus.EXHAUSTED: "mandate.exhausted",
}


class InvalidTransition(Exception):
    """A transition the state machine does not admit."""

    def __init__(
        self, frm: MandateStatus, to: MandateStatus, reason: ReasonCode | None = None
    ) -> None:
        self.frm, self.to, self.reason_code = frm, to, reason
        super().__init__(f"{frm.value} -> {to.value} is not allowed")


@dataclass(frozen=True)
class TransitionResult:
    ok: bool
    frm: MandateStatus | None
    to: MandateStatus
    reason_code: ReasonCode | None = None
    event: str | None = None


def is_allowed(frm: MandateStatus, to: MandateStatus) -> bool:
    return frm in ALLOWED_TRANSITIONS.get(to, frozenset())


def sources_for(to: MandateStatus) -> list[str]:
    """The guard clause for `WHERE status = ANY(...)`.

    Keeping this next to the table is deliberate: the SQL guard and the
    in-memory check must never disagree, so both read the same dictionary.
    """
    return sorted(s.value for s in ALLOWED_TRANSITIONS.get(to, frozenset()))


def refusal_reason(current: MandateStatus, to: MandateStatus) -> ReasonCode:
    """Why a transition was refused, in the buyer's terms.

    "You cannot activate a revoked mandate" is more useful than "invalid
    transition", and it is the string that reaches all three consoles.
    """
    if current in TERMINAL_REASONS:
        return TERMINAL_REASONS[current]
    return ReasonCode.MANDATE_SUSPENDED  # unknown state -> deny, fail closed


def check(frm: MandateStatus, to: MandateStatus) -> None:
    """Raise if the transition is not admitted. For callers that prefer it."""
    if not is_allowed(frm, to):
        raise InvalidTransition(frm, to, refusal_reason(frm, to))
