"""SQLAlchemy mappings owned by the VuelaYa merchant service."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class OfferRow(Base):
    """The shared `offers` table, mapped locally without importing kernel code.

    Columns follow `aval/contracts/fixtures/schema.sql` (decision 0029: the
    fixture is the schema). The legacy attribute name `depart_date` is kept
    for the merchant code paths; it maps to the fixture's `travel_date`
    column, and money is NUMERIC(12,2) in storage — the TEXT DTO lives at
    the `to_offer` seam, never in a bind parameter.
    """

    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str | None] = mapped_column(Text)
    destination: Mapped[str | None] = mapped_column(Text)
    depart_date: Mapped[date | None] = mapped_column("travel_date", Date)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MerchantOrder(Base):
    """One immutable, merchant-signed checkout cart."""

    __tablename__ = "merchant_orders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    offer_id: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    checkout_jwt: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    checkout_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="quoted")
    purchase_id: Mapped[str | None] = mapped_column(Text, unique=True)
    receipt: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
