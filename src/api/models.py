"""SQLAlchemy tables owned by Dev 3 in the kernel.

Mirrors `aval/contracts/fixtures/schema.sql`, which mirrors `schemas.md` §6
plus decision 0021's passkey tables. The DDL is the contract; this is the
mapping.

Note the columns Dev 3 must **never** write: `reserved_amount`, `spent_total`
and `txn_count_period` live on our table but belong to verify (Dev 2), which
updates them inside the atomic reservation. Writing them here would race with
that UPDATE and silently break the budget invariant.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Mandate(Base):
    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    jti: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    claims: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sd_jwt: Mapped[str | None] = mapped_column(Text)

    # --- written ONLY by verify [Dev 2] (schemas.md §6 convention) --------
    reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0)
    spent_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0)
    txn_count_period: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    # ---------------------------------------------------------------------

    parent_jti: Mapped[str | None] = mapped_column(
        Text, ForeignKey("mandates.jti"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    purchase_id: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    diff: Mapped[dict | None] = mapped_column(JSONB)
    timeout_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    decision: Mapped[str | None] = mapped_column(Text)
    approver: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    receipt_sig: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class PaymentInstrument(Base):
    __tablename__ = "payment_instruments"

    token_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_jti: Mapped[str] = mapped_column(Text, nullable=False)
    rail: Mapped[str] = mapped_column(Text, nullable=False, default="yuno_sim")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class WebAuthnCredential(Base):
    """Decision 0021 — absent from the original §6 DDL."""

    __tablename__ = "webauthn_credentials"

    credential_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Monotonic. A value that does not advance means a cloned authenticator.
    sign_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0)
    transports: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    aaguid: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebAuthnChallenge(Base):
    """Single-use. `consumed_at` is what stops a replay."""

    __tablename__ = "webauthn_challenges"

    challenge: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_id: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
