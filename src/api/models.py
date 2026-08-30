"""SQLAlchemy tables owned by Dev 3 in the kernel.

Mirrors `aval/contracts/fixtures/schema.sql`, the one schema in the
repository. The DDL is the contract; this is the mapping.

Money and every timestamp on `mandates`, `escalations` and
`payment_instruments` are TEXT, not NUMERIC/TIMESTAMPTZ — that is the agent
lane's definition, and it wins on every table the two lanes share (see the
schema file's header for why: `src/agent/kernel.py`'s budget reservation is a
compare-and-swap on the *exact previous string value*, and a NUMERIC column
would make that CAS depend on how the server normalises `0.00` against `0`).
`iso_now()` below is this module's half of that contract — every write of a
shared timestamp column goes through it, so the strings sort exactly like the
TIMESTAMPTZ values they replaced.

Note the columns Dev 3 must **never** write: `reserved_amount`, `spent_total`
and `txn_count` live on our table but belong to verify (Dev 2), which updates
them inside the atomic reservation. Writing them here would race with that
UPDATE and silently break the budget invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def iso_now() -> str:
    """The shared lanes' one timestamp format: UTC, second precision, ISO-8601."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Mandate(Base):
    __tablename__ = "mandates"

    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    claims: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str | None] = mapped_column(Text)
    sd_jwt: Mapped[str | None] = mapped_column(Text)

    # --- written ONLY by verify [Dev 2] (schemas.md §6 convention) --------
    reserved_amount: Mapped[str] = mapped_column(Text, nullable=False, default="0.00")
    spent_total: Mapped[str] = mapped_column(Text, nullable=False, default="0.00")
    txn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ---------------------------------------------------------------------

    parent_jti: Mapped[str | None] = mapped_column(Text, ForeignKey("mandates.jti"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=iso_now)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default=iso_now)


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    purchase_id: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_jti: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    diff: Mapped[str | None] = mapped_column(Text)
    timeout_at: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str | None] = mapped_column(Text)
    approver: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    receipt_sig: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=iso_now)


class PaymentInstrument(Base):
    __tablename__ = "payment_instruments"

    token_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_jti: Mapped[str] = mapped_column(Text, nullable=False)
    rail: Mapped[str] = mapped_column(Text, nullable=False, default="yuno_sim")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, default=iso_now)
    deleted_at: Mapped[str | None] = mapped_column(Text)


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str | None] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    depart_date: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    @property
    def amount_decimal(self) -> Decimal:
        return Decimal(self.amount)


class WebAuthnCredential(Base):
    """Decision 0021 — absent from the original §6 DDL."""

    __tablename__ = "webauthn_credentials"

    credential_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Monotonic. A value that does not advance means a cloned authenticator.
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transports: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    aaguid: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebAuthnChallenge(Base):
    """Single-use. `consumed_at` is what stops a replay."""

    __tablename__ = "webauthn_challenges"

    challenge: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_id: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
