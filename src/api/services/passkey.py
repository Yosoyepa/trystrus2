"""The passkey ceremony (decision #3, ADR-003).

The point of this file, in one sentence: **the challenge is the canonical hash
of the mandate**, so the biometric gesture signs that exact permission rather
than "a login happened".

That is what makes the ceremony evidence. A session-scoped challenge would
prove someone was present; a mandate-scoped one proves they agreed to *these*
limits, on *this* merchant, until *this* date. It is also why an agent cannot
forge consent: WebAuthn requires a physical gesture on an authenticator, and
`userVerification: required` means a present device is not enough.

Used for three things (decision #3): creating a mandate, changing its limits,
and revoking it. Revocation demands the same gesture as creation -- taking
authority away must not be easier to forge than granting it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from trustlib.canonical import canonical_hash
from trustlib.jose import b64u_encode
from trustlib.models import MandateClaims

from ..config import Settings, settings


class Purpose(StrEnum):
    REGISTER = "register"
    ACTIVATE = "activate"
    REVOKE = "revoke"


class PasskeyError(Exception):
    """Ceremony failed. Never leaks which step failed to the caller."""


class ChallengeExpired(PasskeyError):
    pass


class ChallengeAlreadyUsed(PasskeyError):
    pass


class UserVerificationMissing(PasskeyError):
    """The authenticator did not verify the human (no biometric/PIN).

    Presence is not consent: a tapped key with no user verification proves a
    device was reachable, not that a person agreed.
    """


class CloneDetected(PasskeyError):
    """The signature counter went backwards -- a copied authenticator."""


@dataclass(frozen=True)
class Challenge:
    value: str                  # base64url
    user_id: str
    purpose: Purpose
    expires_at: datetime
    mandate_id: str | None = None


@dataclass(frozen=True)
class StoredCredential:
    credential_id: str
    user_id: str
    public_key: bytes
    sign_count: int


def mandate_challenge(claims: MandateClaims) -> bytes:
    """SHA-256 over the canonical mandate -- the bytes the human signs.

    Canonical form matters: the same claims serialized two ways would hash two
    ways, and the assertion would stop matching the mandate it authorized.
    """
    return canonical_hash(claims.model_dump(mode="json", exclude_none=True))


class PasskeyService:
    """WebAuthn ceremonies, with the mandate hash as the challenge."""

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or settings()

    # -- registration: the user enrols an authenticator once ---------------
    def registration_options(self, *, user_id: str,
                             user_name: str | None = None) -> tuple[dict, Challenge]:
        options = generate_registration_options(
            rp_id=self._config.rp_id,
            rp_name=self._config.rp_name,
            user_id=user_id.encode(),
            user_name=user_name or user_id,
            authenticator_selection=AuthenticatorSelectionCriteria(
                # Not a preference: without user verification the gesture
                # proves presence, not intent (decision #3).
                user_verification=UserVerificationRequirement.REQUIRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
        )
        challenge = self._issue(options.challenge, user_id, Purpose.REGISTER)
        return options_to_json(options), challenge

    def verify_registration(self, response: dict, challenge: Challenge,
                            ) -> StoredCredential:
        self._assert_usable(challenge)
        try:
            verified = verify_registration_response(
                credential=response,
                expected_challenge=_decode(challenge.value),
                expected_rp_id=self._config.rp_id,
                expected_origin=self._config.rp_origin,
                require_user_verification=True,
            )
        except Exception as exc:
            raise PasskeyError("registration did not verify") from exc

        return StoredCredential(
            credential_id=b64u_encode(verified.credential_id),
            user_id=challenge.user_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
        )

    # -- authentication: the gesture that signs a mandate ------------------
    def mandate_options(
        self,
        *,
        claims: MandateClaims,
        purpose: Purpose,
        credentials: list[StoredCredential],
    ) -> tuple[dict, Challenge]:
        """Options whose challenge IS the mandate's canonical hash.

        This is the whole idea. A generic random challenge would authenticate
        the user; this one binds the gesture to the permission, so the
        assertion is later replayable as evidence of what was agreed.
        """
        digest = mandate_challenge(claims)
        options = generate_authentication_options(
            rp_id=self._config.rp_id,
            challenge=digest,
            user_verification=UserVerificationRequirement.REQUIRED,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=_decode(c.credential_id))
                for c in credentials
            ],
        )
        challenge = self._issue(digest, claims.sub, purpose,
                                mandate_id=claims.jti)
        return options_to_json(options), challenge

    def verify_assertion(
        self,
        response: dict,
        *,
        challenge: Challenge,
        credential: StoredCredential,
        claims: MandateClaims | None = None,
    ) -> int:
        """Verify an assertion. Returns the new signature counter.

        Order: challenge freshness, then the signature, then the counter.
        Checking the counter first would leak whether a credential exists.
        """
        self._assert_usable(challenge)

        # The challenge must still be the hash of the mandate being acted on.
        # Without this, an assertion collected for mandate A could authorize
        # mandate B.
        if claims is not None:
            expected = b64u_encode(mandate_challenge(claims))
            if challenge.value != expected:
                raise PasskeyError("challenge does not match this mandate")

        try:
            verified = verify_authentication_response(
                credential=response,
                expected_challenge=_decode(challenge.value),
                expected_rp_id=self._config.rp_id,
                expected_origin=self._config.rp_origin,
                credential_public_key=credential.public_key,
                credential_current_sign_count=credential.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            raise PasskeyError("assertion did not verify") from exc

        # A counter that does not advance means the authenticator was cloned:
        # two copies of the same credential, each with its own count.
        # Authenticators that always report 0 are exempt -- that is allowed by
        # the spec and common on platform authenticators.
        if credential.sign_count > 0 and verified.new_sign_count <= credential.sign_count:
            raise CloneDetected(
                f"sign_count did not advance "
                f"({credential.sign_count} -> {verified.new_sign_count})"
            )
        return verified.new_sign_count

    # -- challenge lifetime ------------------------------------------------
    def _issue(self, digest: bytes, user_id: str, purpose: Purpose,
               *, mandate_id: str | None = None) -> Challenge:
        return Challenge(
            value=b64u_encode(digest),
            user_id=user_id,
            purpose=purpose,
            mandate_id=mandate_id,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self._config.challenge_ttl_seconds),
        )

    @staticmethod
    def _assert_usable(challenge: Challenge) -> None:
        if datetime.now(UTC) > challenge.expires_at:
            raise ChallengeExpired("challenge expired")


def _decode(value: str) -> bytes:
    from trustlib.jose import b64u_decode

    return b64u_decode(value)


def random_challenge() -> bytes:
    return secrets.token_bytes(32)
