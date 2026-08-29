"""The simulated Yuno-style AP2 payment orchestrator (decision 0024).

**This is not Yuno.** It is a proposal for what a payment orchestrator's AP2
surface could look like, built because no provider has shipped one: PayPal,
Adyen, Worldpay, Mastercard and Amex have all announced AP2 support and
published no endpoints. Every response carries `simulated: true`, and the
service says so in its own name.

What it does that no real rail does today: it verifies the AP2 Payment Mandate
itself before settling — issuer signature, temporal window, checkout binding,
and whether the mandate is still live — instead of charging because a merchant
asked (`ap2_verifier.py`).

Run: `uv run uvicorn yuno_sim.main:app --app-dir src --port 8002`
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from trustlib.models import ReasonCode

from . import disputes, vault
from .ap2_verifier import AP2Verifier
from .config import settings
from .payments import PaymentRefused, capture
from .schemas import (
    CaptureRequest,
    DisputeRequest,
    DisputeView,
    EnrollRequest,
    PaymentTokenView,
    ReceiptView,
    SetupTokenView,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(
    title=settings().provider_name,
    version="1.0.0",
    description=(
        "A SIMULATED payment orchestrator that speaks AP2. Not a real "
        "provider. Verifies the Payment Mandate before settling — issuer "
        "signature, temporal window, checkout binding, and live mandate "
        "status. See aval/docs/decisions/0024."
    ),
)

_engine = None
_factory: async_sessionmaker[AsyncSession] | None = None
_verifier: AP2Verifier | None = None


def _session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _factory
    if _factory is None:
        _engine = create_async_engine(settings().database_url, pool_size=5,
                                      max_overflow=2, pool_pre_ping=True)
        _factory = async_sessionmaker(_engine, expire_on_commit=False,
                                      autoflush=False)
    return _factory


def verifier() -> AP2Verifier:
    global _verifier
    if _verifier is None:
        _verifier = AP2Verifier()
    return _verifier


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@app.middleware("http")
async def mark_as_simulated(request: Request, call_next):
    """Every response says what this is. Not decoration — the honest half of
    decision 0024, which only works if nobody can mistake it for a real rail.
    """
    response = await call_next(request)
    response.headers["X-Aval-Simulated"] = "true"
    response.headers["X-Aval-Provider"] = settings().provider_name
    return response


@app.exception_handler(PaymentRefused)
async def refusal_handler(_: Request, exc: PaymentRefused) -> JSONResponse:
    """402 with a reason code — the contract's refusal shape (api.yaml)."""
    return JSONResponse(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        content={"reason_code": exc.reason_code.value, "message": str(exc),
                 "simulated": True},
    )


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "yuno_sim", "simulated": True,
            "provider": settings().provider_name}


# ==========================================================================
# Enrollment — the human approves the instrument once
# ==========================================================================
@app.post("/v1/payment-methods/enroll", response_model=SetupTokenView,
          tags=["vault"])
async def enroll(body: EnrollRequest,
                 session: AsyncSession = Depends(get_session)):
    row = await vault.create_setup_token(session, body.mandate_id)
    return SetupTokenView(setup_token_id=row.setup_token_id,
                          approve_url=row.approve_url,
                          expires_at=row.expires_at)


@app.get("/simulated-approval/{setup_token_id}", response_class=HTMLResponse,
         tags=["vault"])
async def approval_page(setup_token_id: str) -> str:
    """Stands in for the provider's own approval screen.

    The thinnest part of the simulation, and worth naming: in the real flow
    the buyer authenticates at the provider. We cannot make a judge
    authenticate against a provider that does not exist.
    """
    return f"""<!doctype html><meta charset=utf-8>
<title>Simulated approval</title>
<body style="font-family:system-ui;max-width:34rem;margin:4rem auto">
<p style="background:#fee;border:1px solid #c00;padding:.75rem">
<strong>Simulated.</strong> This stands in for a payment provider's approval
screen. No real instrument is involved.</p>
<h1>Approve this agent's payment method?</h1>
<p>Setup token <code>{setup_token_id}</code></p>
<form method="post" action="/v1/payment-methods/{setup_token_id}/approve">
<button style="padding:.6rem 1.2rem;font-size:1rem">Approve</button></form>
</body>"""


@app.post("/v1/payment-methods/{setup_token_id}/approve", tags=["vault"])
async def approve(setup_token_id: str,
                  session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await vault.approve_setup_token(session, setup_token_id)
    except vault.VaultError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"status": "approved", "setup_token_id": setup_token_id,
            "simulated": True}


@app.post("/v1/payment-methods/{setup_token_id}/confirm",
          response_model=PaymentTokenView, tags=["vault"])
async def confirm(setup_token_id: str,
                  session: AsyncSession = Depends(get_session)):
    """Approved setup token -> durable payment token."""
    try:
        token = await vault.exchange(session, setup_token_id)
    except vault.VaultError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return PaymentTokenView(token_id=token.token_id,
                            mandate_id=token.mandate_id,
                            instrument_label=token.instrument_label)


@app.delete("/v1/payment-methods/{token_id}", tags=["vault"])
async def delete_token(token_id: str,
                       session: AsyncSession = Depends(get_session)) -> dict:
    """The rail-side kill switch. Idempotent by contract.

    Revocation retries, and a second DELETE that errored would make a
    successful revocation look failed — so an already-deleted token is a
    normal outcome reported as such, not a 4xx.
    """
    deleted = await vault.delete_token(session, token_id)
    if not deleted and not await vault.token_exists(session, token_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown payment token")
    return {"token_id": token_id, "status": "deleted",
            "already_deleted": not deleted, "simulated": True}


# ==========================================================================
# Settlement
# ==========================================================================
@app.post("/v1/payments", response_model=ReceiptView, tags=["payments"])
async def create_payment(
    body: CaptureRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
):
    """Charge a vaulted instrument, after verifying the AP2 Payment Mandate.

    `Idempotency-Key` is required, not optional: without it a retried charge
    is a second charge, and "the same request sent twice buys once" is one of
    the five things FOUNDATION.md says the platform must get right.
    """
    try:
        amount = Decimal(body.amount)
    except InvalidOperation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "amount must be a decimal string") from exc

    settlement = await capture(
        session, verifier(),
        token_id=body.token_id, amount=amount, currency=body.currency,
        idempotency_key=idempotency_key, intent_ref=body.intent_ref,
        purchase_id=body.purchase_id or body.intent_ref,
        mandate_sd_jwt=body.mandate_sd_jwt, checkout_jwt=body.checkout_jwt,
    )
    return ReceiptView(**settlement.to_receipt(
        purchase_id=body.purchase_id or body.intent_ref))


# ==========================================================================
# Disputes
# ==========================================================================
@app.post("/v1/payments/{payment_id}/disputes", response_model=DisputeView,
          tags=["disputes"])
async def open_dispute(payment_id: str, body: DisputeRequest,
                       session: AsyncSession = Depends(get_session)):
    try:
        row = await disputes.open_dispute(session, payment_id,
                                          reason=body.reason)
    except disputes.DisputeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return DisputeView(dispute_id=row.dispute_id, capture_id=row.payment_id,
                       reason=row.reason, status=row.status)


@app.post("/v1/disputes/{dispute_id}/adjudicate", response_model=DisputeView,
          tags=["disputes"])
async def adjudicate(dispute_id: str, evidence: dict,
                     session: AsyncSession = Depends(get_session)):
    """Decide the dispute from the evidence bundle (T18, with Dev 2)."""
    try:
        row = await disputes.adjudicate(session, dispute_id, evidence)
    except disputes.DisputeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return DisputeView(dispute_id=row.dispute_id, capture_id=row.payment_id,
                       reason=row.reason, status=row.status,
                       outcome=row.outcome,
                       findings=(row.evidence or {}).get("findings", []))
