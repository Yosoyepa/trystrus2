"""VuelaYa's persistent catalogue and the hot-price demo control."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib.models import Offer

from .config import Settings, settings
from .models import OfferRow


def to_offer(row: OfferRow) -> Offer:
    """Return the frozen API DTO; no SQLAlchemy object leaks across the seam."""
    return Offer(
        offer_id=row.id,
        merchant_id=row.merchant_id,
        category=row.category,
        title=row.title,
        amount=f"{row.amount:.2f}",
        currency=row.currency,
        origin=row.origin,
        destination=row.destination,
        date=row.travel_date.isoformat() if row.travel_date else None,
        description=row.description,
    )


async def seed_initial_offers(session: AsyncSession,
                              config: Settings | None = None) -> int:
    """Load immutable fixture offers exactly once, without overwriting prices.

    A watcher demo intentionally mutates a price while the service is alive.
    Startup must never undo that mutation, so fixtures use `ON CONFLICT DO
    NOTHING` rather than an upsert.
    """
    config = config or settings()
    loaded = 0
    for name in ("offers.json", "offers_adversarial.json"):
        path = Path(config.fixtures_dir) / name
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        entries = raw.get("offers", []) if isinstance(raw, dict) else raw
        for entry in entries:
            offer = Offer.model_validate(entry)
            travel_date = date.fromisoformat(offer.date) if offer.date else None
            statement = insert(OfferRow).values(
                id=offer.offer_id,
                merchant_id=offer.merchant_id,
                category=offer.category,
                title=offer.title,
                amount=offer.amount_decimal,
                currency=offer.currency,
                origin=offer.origin.upper() if offer.origin else None,
                destination=offer.destination.upper() if offer.destination else None,
                travel_date=travel_date,
                description=offer.description,
                active=True,
            ).on_conflict_do_nothing(index_elements=[OfferRow.id])
            result = await session.execute(statement)
            loaded += result.rowcount or 0
    return loaded


async def list_offers(
    session: AsyncSession,
    *,
    origin: str | None = None,
    destination: str | None = None,
    travel_date: date | None = None,
) -> list[Offer]:
    statement: Select = select(OfferRow).where(OfferRow.active.is_(True))
    if origin:
        statement = statement.where(OfferRow.origin == origin.upper())
    if destination:
        statement = statement.where(OfferRow.destination == destination.upper())
    if travel_date:
        statement = statement.where(OfferRow.travel_date == travel_date)
    statement = statement.order_by(OfferRow.amount.asc(), OfferRow.id.asc())
    rows = (await session.execute(statement)).scalars().all()
    return [to_offer(row) for row in rows]


async def get_offer(session: AsyncSession, offer_id: str) -> Offer | None:
    row = await session.get(OfferRow, offer_id)
    if row is None or not row.active:
        return None
    return to_offer(row)


async def update_price(session: AsyncSession, offer_id: str,
                       amount: Decimal) -> Offer | None:
    """Mutate only an active offer; inactive inventory cannot reappear by price."""
    result = await session.execute(
        update(OfferRow)
        .where(OfferRow.id == offer_id, OfferRow.active.is_(True))
        .values(amount=amount)
        .returning(OfferRow)
    )
    row = result.scalar_one_or_none()
    return to_offer(row) if row is not None else None
