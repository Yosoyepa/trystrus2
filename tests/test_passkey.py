"""The passkey ceremony -- the proof of human intent (decision #3).

The property under test is not "WebAuthn works" (py_webauthn's problem). It is
that **the challenge is the mandate**, so the gesture is evidence of agreement
to specific limits rather than evidence that someone logged in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from api.config import Settings
from api.services.passkey import (
    Challenge,
    ChallengeExpired,
    CloneDetected,
    PasskeyError,
    PasskeyService,
    Purpose,
    StoredCredential,
    mandate_challenge,
)
from trustlib import fake
from trustlib.jose import b64u_encode


@pytest.fixture
def service():
    return PasskeyService(Settings(rp_id="localhost", rp_origin="http://localhost:5173"))


@pytest.fixture
def mandate():
    return fake.mandate()


# ==========================================================================
# The challenge is the mandate
# ==========================================================================
def test_challenge_is_the_canonical_hash_of_the_mandate(mandate):
    """Not a session, not a nonce: these exact limits, this merchant."""
    digest = mandate_challenge(mandate)

    assert len(digest) == 32
    assert digest == mandate_challenge(mandate)  # stable


def test_changing_any_term_changes_the_challenge(mandate):
    """If the limit moves, the old gesture no longer authorizes it."""
    original = mandate_challenge(mandate)

    raised = mandate.model_copy(
        update={"limits": mandate.limits.model_copy(update={"max_per_txn": Decimal("5000")})}
    )
    assert mandate_challenge(raised) != original

    other_merchant = mandate.model_copy(
        update={"scope": mandate.scope.model_copy(update={"merchants": ["not-vuelaya"]})}
    )
    assert mandate_challenge(other_merchant) != original

    later = mandate.model_copy(update={"exp": mandate.exp + 86400})
    assert mandate_challenge(later) != original


def test_challenge_is_independent_of_field_order(mandate):
    """Canonical JSON: the same permission hashes the same however it is built.

    Without this, a mandate rebuilt from the database could hash differently
    from the one the buyer signed, and the assertion would stop matching.
    """
    reordered = mandate.model_validate(
        dict(reversed(list(mandate.model_dump(mode="json").items())))
    )

    assert mandate_challenge(reordered) == mandate_challenge(mandate)


# ==========================================================================
# Ceremony options
# ==========================================================================
def test_registration_requires_user_verification(service):
    """Presence is not consent. An unverified tap must not create a mandate."""
    options, challenge = service.registration_options(user_id="usr_marta")

    assert '"userVerification": "required"' in options.replace("'", '"')
    assert challenge.purpose is Purpose.REGISTER


def test_mandate_options_carry_the_mandate_hash(service, mandate):
    credential = StoredCredential("cred-1", "usr_marta", b"\x00" * 32, 0)

    _, challenge = service.mandate_options(
        claims=mandate, purpose=Purpose.ACTIVATE, credentials=[credential]
    )

    assert challenge.value == b64u_encode(mandate_challenge(mandate))
    assert challenge.mandate_id == mandate.jti
    assert challenge.user_id == mandate.sub


def test_revocation_uses_the_same_ceremony_as_creation(service, mandate):
    """Taking authority away must not be easier to forge than granting it."""
    credential = StoredCredential("cred-1", "usr_marta", b"\x00" * 32, 0)

    _, activate = service.mandate_options(
        claims=mandate, purpose=Purpose.ACTIVATE, credentials=[credential]
    )
    _, revoke = service.mandate_options(
        claims=mandate, purpose=Purpose.REVOKE, credentials=[credential]
    )

    assert revoke.value == activate.value  # same mandate, same bytes signed
    assert revoke.purpose is Purpose.REVOKE  # different intent recorded


# ==========================================================================
# Challenge lifetime
# ==========================================================================
def test_expired_challenge_is_refused(service, mandate):
    stale = Challenge(
        value=b64u_encode(mandate_challenge(mandate)),
        user_id="usr_marta",
        purpose=Purpose.ACTIVATE,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    credential = StoredCredential("cred-1", "usr_marta", b"\x00" * 32, 0)

    with pytest.raises(ChallengeExpired):
        service.verify_assertion({}, challenge=stale, credential=credential)


def test_an_assertion_for_one_mandate_cannot_authorize_another(service, mandate):
    """The attack this check exists for.

    Collect a legitimate assertion for a $150 mandate, then present it against
    a $5000 one. The challenge no longer matches the claims, so it dies before
    the signature is even considered.
    """
    other = fake.mandate(max_per_txn="5000")
    challenge_for_first = Challenge(
        value=b64u_encode(mandate_challenge(mandate)),
        user_id=mandate.sub,
        purpose=Purpose.ACTIVATE,
        mandate_id=mandate.jti,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    credential = StoredCredential("cred-1", mandate.sub, b"\x00" * 32, 0)

    with pytest.raises(PasskeyError, match="does not match this mandate"):
        service.verify_assertion(
            {}, challenge=challenge_for_first, credential=credential, claims=other
        )


# ==========================================================================
# Clone detection
# ==========================================================================
def test_sign_count_going_backwards_is_a_clone(service, mandate, monkeypatch):
    """Two copies of one credential each keep their own counter."""
    import api.services.passkey as module

    class Verified:
        new_sign_count = 5

    monkeypatch.setattr(module, "verify_authentication_response", lambda **_: Verified())

    challenge = Challenge(
        value=b64u_encode(mandate_challenge(mandate)),
        user_id=mandate.sub,
        purpose=Purpose.ACTIVATE,
        mandate_id=mandate.jti,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    stale_credential = StoredCredential("cred-1", mandate.sub, b"\x00" * 32, 9)

    with pytest.raises(CloneDetected):
        service.verify_assertion(
            {}, challenge=challenge, credential=stale_credential, claims=mandate
        )


def test_authenticators_that_always_report_zero_are_accepted(service, mandate, monkeypatch):
    """Platform authenticators legitimately keep no counter -- allowed by spec."""
    import api.services.passkey as module

    class Verified:
        new_sign_count = 0

    monkeypatch.setattr(module, "verify_authentication_response", lambda **_: Verified())

    challenge = Challenge(
        value=b64u_encode(mandate_challenge(mandate)),
        user_id=mandate.sub,
        purpose=Purpose.ACTIVATE,
        mandate_id=mandate.jti,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    credential = StoredCredential("cred-1", mandate.sub, b"\x00" * 32, 0)

    assert (
        service.verify_assertion({}, challenge=challenge, credential=credential, claims=mandate)
        == 0
    )


# ==========================================================================
# The platform constraint that costs money (ADR-018)
# ==========================================================================
def test_rp_id_is_configurable_because_run_app_cannot_host_passkeys():
    """`*.run.app` is on the Public Suffix List, so WebAuthn rejects it.

    This is why the domain is a day-0 purchase, and why rp_id is settings and
    not a constant.
    """
    assert Settings().rp_id == "localhost"
    assert Settings(rp_id="aval.app").rp_id == "aval.app"
