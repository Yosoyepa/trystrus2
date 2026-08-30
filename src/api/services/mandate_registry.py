"""Mandate issuance and verification -- `trustlib.MandateRegistry`.

This is the object the whole project is named after: a permission that can be
written down, checked by a stranger, and taken away.

"Checked by a stranger" is the demanding part. The merchant verifies the
signature against our published JWKS *without asking us* (decision #6); if it
had to trust our answer we would be the single point of trust the challenge
exists to remove.

Every mandate is issued in AP2 shape (decision 0023): it carries
`vct: mandate.payment.open.1` and a `constraints[]` projection of its own
limits, derived from the native fields so the two can never disagree.
"""

from __future__ import annotations

import time

from trustlib import ap2, ids, sdjwt
from trustlib.jose import generate_ed25519, public_jwk
from trustlib.models import (
    ConfirmationKey,
    IssuedMandate,
    MandateClaims,
    MandateClaimsInput,
)

from ..config import Settings, settings
from .keys import KeyStore, key_store

# Claims the buyer may keep back until a merchant actually needs them.
# Minimising what travels is the point of SD-JWT (schemas.md §1).
SELECTIVE_CLAIMS = ("email", "shipping_address")


class MandateRegistry:
    """Issues SD-JWT mandates and verifies them against the published JWKS."""

    def __init__(self, keys: KeyStore | None = None,
                 config: Settings | None = None) -> None:
        self._keys = keys or key_store()
        self._config = config or settings()

    # -- issuance ----------------------------------------------------------
    def build_claims(self, request: MandateClaimsInput, *,
                     jti: str | None = None,
                     parent_jti: str | None = None) -> MandateClaims:
        """Assemble the claims that the buyer's passkey will sign.

        Built *before* the ceremony, because the challenge is this object's
        canonical hash -- the gesture has to sign something that already
        exists in final form.
        """
        agent_jwk = request.agent_jwk
        if agent_jwk is None:
            # Convenience for the demo: mint the agent's key pair here. A real
            # agent generates its own and never shares the private half
            # (decision #9's stated limit).
            agent_jwk = public_jwk(generate_ed25519())

        return ap2.apply_ap2_projection(
            MandateClaims(
                iss=self._config.issuer,
                iat=int(request.validity.not_before.timestamp()),
                nbf=int(request.validity.not_before.timestamp()),
                exp=int(request.validity.expires_at.timestamp()),
                jti=jti or ids.new_id(ids.MANDATE),
                sub=request.user_id,
                agent=request.agent_id,
                cnf=ConfirmationKey(jwk=agent_jwk),
                payment_method_ref=request.payment_method_ref,
                currency=request.currency,
                scope=request.scope,
                conditions=request.conditions,
                limits=request.limits,
                validity=request.validity,
                parent_jti=parent_jti,
            )
        )

    def sign(self, claims: MandateClaims, *,
             disclose: dict | None = None) -> IssuedMandate:
        """Sign assembled claims into an SD-JWT.

        Separate from `build_claims` on purpose: the passkey ceremony happens
        between the two, and signing before the human has agreed would produce
        a valid mandate nobody authorized.
        """
        signing = self._keys.issuer_key()
        payload = claims.model_dump(mode="json", exclude_none=True)

        sd_jwt = sdjwt.issue(
            payload, signing.key, kid=signing.kid,
            selective={k: v for k, v in (disclose or {}).items()
                       if k in SELECTIVE_CLAIMS} or None,
        )
        return IssuedMandate(sd_jwt=sd_jwt, jti=claims.jti, claims=claims)

    def issue(self, claims: MandateClaimsInput) -> IssuedMandate:
        """`MandateRegistry.issue` from schemas.md §3.

        The one-shot path, for tests and fixtures. The real flow goes through
        `build_claims` -> passkey ceremony -> `sign`, because a mandate must
        not exist in signed form before a human has agreed to it.
        """
        return self.sign(self.build_claims(claims))

    def derive(self, parent: MandateClaims, *, limits, ttl_seconds: int = 3600,
               ) -> MandateClaims:
        """A sticky mini-mandate: narrower limits, linked to its parent.

        schemas.md §5.3 -- when a human approves an escalation with
        `sticky: true`, the agent gets a *new* mandate with tightened limits
        rather than a relaxation of the original. The parent's authority is
        never widened; that is what makes approving safe.
        """
        now = int(time.time())
        return ap2.apply_ap2_projection(
            parent.model_copy(update={
                "jti": ids.new_id(ids.MANDATE),
                "parent_jti": parent.jti,
                "limits": limits,
                "iat": now,
                "nbf": now,
                "exp": min(now + ttl_seconds, parent.exp),  # never outlives its parent
            })
        )

    # -- verification ------------------------------------------------------
    def verify(self, sd_jwt: str, *, nonce: str | None = None,
               aud: str | None = None,
               require_key_binding: bool = False) -> MandateClaims:
        """`MandateRegistry.verify` from schemas.md §3.

        Checks the issuer signature, the temporal window, the disclosures and
        -- when presented or required -- the key binding that proves the
        holder controls `cnf.jwk`.
        """
        claims = sdjwt.verify(
            sd_jwt, self._keys.verification_keys(),
            nonce=nonce, aud=aud, require_key_binding=require_key_binding,
        )
        return MandateClaims.model_validate(claims)

    def jwks(self) -> dict:
        """`GET /.well-known/jwks.json` -- how a stranger checks our work."""
        return self._keys.jwks()
