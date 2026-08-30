"""The VuelaYa HTTP service: catalogue, signed checkout and rail webhooks."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib.models import ReasonCode, Receipt

from . import catalog, deps
from .charge import ChargeRefused, ChargeSettlementError
from .config import settings
from .db import get_session, session_factory
from .schemas import ChargeRequest, CheckoutQuote, CheckoutQuoteRequest, PriceUpdate
from .webhooks import WebhookVerificationError, YunoWebhookVerifier

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Seed only inserts missing fixture rows; it cannot undo a live price
    # change made through the judge-facing admin endpoint.
    async with session_factory()() as session:
        await catalog.seed_initial_offers(session)
        await session.commit()
    yield


app = FastAPI(
    title="VuelaYa — Aval merchant",
    version="1.1.0",
    description=(
        "VuelaYa catalogue and AP2 checkout. The only money route is "
        "checkout/charge after kernel verify APPROVED."
    ),
    lifespan=lifespan,
)

_webhook_verifier: YunoWebhookVerifier | None = None


def webhook_verifier() -> YunoWebhookVerifier:
    global _webhook_verifier
    if _webhook_verifier is None:
        _webhook_verifier = YunoWebhookVerifier()
    return _webhook_verifier


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "merchant", "merchant": "vuelaya"}


@app.get("/catalog/offers", response_model=list[dict], tags=["merchant"])
async def offers(
    origin: str | None = None,
    destination: str | None = None,
    date: date | None = None,
    session: AsyncSession = Depends(get_session),
):
    return [item.model_dump(mode="json") for item in await catalog.list_offers(
        session, origin=origin, destination=destination, travel_date=date)]


@app.get("/catalog/offers/{offer_id}", response_model=dict, tags=["merchant"])
async def offer(offer_id: str, session: AsyncSession = Depends(get_session)):
    result = await catalog.get_offer(session, offer_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active offer with this id")
    return result.model_dump(mode="json")


@app.post("/admin/offers/{offer_id}/price", response_model=dict, tags=["merchant"])
async def set_price(offer_id: str, body: PriceUpdate,
                    session: AsyncSession = Depends(get_session)):
    result = await catalog.update_price(session, offer_id, body.amount_decimal)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active offer with this id")
    return result.model_dump(mode="json")


@app.post("/checkout/quote", status_code=status.HTTP_201_CREATED,
          response_model=CheckoutQuote, tags=["merchant"])
async def quote_checkout(body: CheckoutQuoteRequest,
                         session: AsyncSession = Depends(get_session)):
    result = await catalog.get_offer(session, body.offer_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active offer with this id")
    return await deps.checkout().quote(session, result)


@app.post("/checkout/charge", response_model=Receipt, tags=["merchant"])
async def charge(body: ChargeRequest,
                 session: AsyncSession = Depends(get_session)):
    try:
        return await deps.charge_service().charge(session, body)
    except ChargeRefused as exc:
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content={"reason_code": exc.reason_code.value, "message": str(exc),
                     "purchase_id": body.purchase_id},
        )
    except ChargeSettlementError as exc:
        log.error("capture outcome needs reconciliation for %s", body.purchase_id,
                  exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"reason_code": ReasonCode.RAIL_ERROR.value,
                     "message": "capture outcome is pending reconciliation; "
                                "retry with the same purchase_id",
                     "purchase_id": body.purchase_id},
        )


@app.post("/webhooks/yuno", tags=["merchant"])
async def yuno_webhook(request: Request):
    try:
        event = await webhook_verifier().verify(
            headers=dict(request.headers), body=await request.body())
    except WebhookVerificationError as exc:
        # T14's key property: invalid signatures do not become a harmless 200.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"reason_code": ReasonCode.INVALID_SIGNATURE.value,
                     "message": str(exc)},
        )
    log.info("accepted signed Yuno webhook %s (%s)", event.event_id, event.type)
    return {"status": "accepted", "event_id": event.event_id}


@app.post("/webhooks/paypal", deprecated=True, tags=["merchant"])
async def legacy_paypal_webhook():
    """Kept as an explicit 410 bridge while consumers move to /webhooks/yuno."""
    raise HTTPException(status.HTTP_410_GONE,
                        "PayPal is not Aval's active rail; use /webhooks/yuno")
