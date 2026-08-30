"""The VuelaYa HTTP service: catalogue, signed checkout and rail webhooks."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from trustlib.models import ReasonCode, Receipt

from . import catalog, deps
from mcp.server.transport_security import TransportSecuritySettings

from .mcp_server import mcp
from .charge import ChargeRefused, ChargeSettlementError
from .db import get_session, session_factory
from .schemas import ChargeRequest, CheckoutQuote, CheckoutQuoteRequest, PriceUpdate
from .webhooks import WebhookVerificationError, YunoWebhookVerifier

log = logging.getLogger(__name__)


# The MCP tools were reachable over stdio only, which is a same-machine
# transport: in a deployed service nothing was listening, so the agent could
# never speak MCP to this merchant. Mounting the streamable-HTTP app puts the
# same three tools on the network without a second deployable -- they inherit
# this service's ingress, its Cloud SQL socket and its TLS.
# DNS-rebinding protection ships ON with an EMPTY allowlist, and an empty
# allowlist rejects every Host -- deployed behind a load balancer that is a
# uniform 421, which reads as "the MCP is down" rather than "the MCP refused
# your Host header". So the allowlist is explicit and comes from the
# environment: TT_MCP_ALLOWED_HOSTS is the deployed hostname (merchant.<domain>,
# plus the *.run.app name if the service is reached directly). Unset, it falls
# back to loopback, which is what a developer on this machine needs and what a
# public deployment must never silently be.
_MCP_ALLOWED_HOSTS = [h.strip() for h in os.environ.get("TT_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_MCP_HOSTS = _MCP_ALLOWED_HOSTS or ["127.0.0.1:*", "localhost:*"]

mcp_app = mcp.streamable_http_app(
    # Mounted under /mcp below; leaving the default here would serve /mcp/mcp.
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        allowed_hosts=_MCP_HOSTS,
        allowed_origins=[f"https://{h}" for h in _MCP_HOSTS] + [f"http://{h}" for h in _MCP_HOSTS],
    ),
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Seed only inserts missing fixture rows; it cannot undo a live price
    # change made through the judge-facing admin endpoint.
    async with session_factory()() as session:
        await catalog.seed_initial_offers(session)
        await session.commit()
    # The transport owns a session manager that has to be started with the app.
    # Mounting the ASGI app without running its lifespan leaves that manager
    # uninitialised, and every tools/call fails at runtime rather than at boot.
    async with mcp_app.router.lifespan_context(mcp_app):
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

# The same three tools src/merchant/mcp_server.py already defines, now on the
# network. `request_purchase` still goes through the kernel: this adds a
# transport, not a second way to move money.
app.mount("/mcp", mcp_app)

_webhook_verifier: YunoWebhookVerifier | None = None


def webhook_verifier() -> YunoWebhookVerifier:
    global _webhook_verifier
    if _webhook_verifier is None:
        _webhook_verifier = YunoWebhookVerifier()
    return _webhook_verifier


@app.get("/health", tags=["ops"])
@app.get("/healthz", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "merchant", "merchant": "vuelaya"}


@app.get("/catalog/offers", response_model=list[dict], tags=["merchant"])
async def offers(
    origin: str | None = None,
    destination: str | None = None,
    date: date | None = None,
    session: AsyncSession = Depends(get_session),
):
    return [
        item.model_dump(mode="json")
        for item in await catalog.list_offers(
            session, origin=origin, destination=destination, travel_date=date
        )
    ]


@app.get("/catalog/offers/{offer_id}", response_model=dict, tags=["merchant"])
async def offer(offer_id: str, session: AsyncSession = Depends(get_session)):
    result = await catalog.get_offer(session, offer_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active offer with this id")
    return result.model_dump(mode="json")


@app.post("/admin/offers/{offer_id}/price", response_model=dict, tags=["merchant"])
async def set_price(offer_id: str, body: PriceUpdate, session: AsyncSession = Depends(get_session)):
    result = await catalog.update_price(session, offer_id, body.amount_decimal)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active offer with this id")
    return result.model_dump(mode="json")


@app.post(
    "/checkout/quote",
    status_code=status.HTTP_201_CREATED,
    response_model=CheckoutQuote,
    tags=["merchant"],
)
async def quote_checkout(body: CheckoutQuoteRequest, session: AsyncSession = Depends(get_session)):
    result = await catalog.get_offer(session, body.offer_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active offer with this id")
    return await deps.checkout().quote(session, result)


@app.post("/checkout/charge", response_model=Receipt, tags=["merchant"])
async def charge(body: ChargeRequest, session: AsyncSession = Depends(get_session)):
    try:
        return await deps.charge_service().charge(session, body)
    except ChargeRefused as exc:
        return JSONResponse(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            content={
                "reason_code": exc.reason_code.value,
                "message": str(exc),
                "purchase_id": body.purchase_id,
            },
        )
    except ChargeSettlementError:
        log.error("capture outcome needs reconciliation for %s", body.purchase_id, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "reason_code": ReasonCode.RAIL_ERROR.value,
                "message": "capture outcome is pending reconciliation; "
                "retry with the same purchase_id",
                "purchase_id": body.purchase_id,
            },
        )


@app.post("/webhooks/yuno", tags=["merchant"])
async def yuno_webhook(request: Request):
    try:
        event = await webhook_verifier().verify(
            headers=dict(request.headers), body=await request.body()
        )
    except WebhookVerificationError as exc:
        # T14's key property: invalid signatures do not become a harmless 200.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"reason_code": ReasonCode.INVALID_SIGNATURE.value, "message": str(exc)},
        )
    log.info("accepted signed Yuno webhook %s (%s)", event.event_id, event.type)
    return {"status": "accepted", "event_id": event.event_id}


@app.post("/webhooks/paypal", deprecated=True, tags=["merchant"])
async def legacy_paypal_webhook():
    """Kept as an explicit 410 bridge while consumers move to /webhooks/yuno."""
    raise HTTPException(
        status.HTTP_410_GONE, "PayPal is not Aval's active rail; use /webhooks/yuno"
    )
