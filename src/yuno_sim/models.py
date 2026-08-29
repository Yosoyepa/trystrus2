"""The orchestrator's own books.

Mirrors the `yuno_*` block of `aval/contracts/fixtures/schema.sql`.

Nothing here stores a PAN, a CVV or any card data — only opaque vaulted token
ids and a human-readable label like "VISA ****4242". That is not a
simplification of the simulation; it is the property the real design has
(decision #8's "never touch a card"), and a simulation that stored card data
would be modelling the wrong thing.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SetupTokenRow(Base):
    __tablename__ = "yuno_setup_tokens"

    setup_token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    approve_url: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class PaymentTokenRow(Base):
    """The vaulted instrument. Deleting it is the rail-side kill switch."""

    __tablename__ = "yuno_payment_tokens"

    token_id: Mapped[str] = mapped_column(Text, primary_key=True)
    setup_token_id: Mapped[str | None] = mapped_column(Text)
    mandate_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    instrument_label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentRow(Base):
    __tablename__ = "yuno_payments"

    payment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    token_id: Mapped[str] = mapped_column(Text, nullable=False)
    mandate_jti: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    # Recorded even on refusal: the trail should show that the binding was
    # checked, not merely that a payment failed.
    checkout_hash: Mapped[str | None] = mapped_column(Text)
    intent_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class IdempotencyRow(Base):
    """Same key, same answer. The primary key is the enforcement."""

    __tablename__ = "yuno_idempotency"

    idempotency_key: Mapped[str] = mapped_column(Text, primary_key=True)
    payment_id: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class DisputeRow(Base):
    __tablename__ = "yuno_disputes"

    dispute_id: Mapped[str] = mapped_column(Text, primary_key=True)
    payment_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False,
                                        default="UNAUTHORISED")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    outcome: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
