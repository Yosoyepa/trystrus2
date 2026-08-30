"""The kernel service — `api` in the plans, `src/api/` here.

Hosts routers from two workstreams in separate folders with no cross-imports
outside `trustlib` (decision #19): identity and escalations are Dev 3's,
verify/purchases/audit/events are Dev 2's, and Dev 4's Telegram webhook mounts
here too.

Run: `uv run uvicorn api.main:app --app-dir src --reload --port 8001`
"""

from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import escalations, mandates
from .services.escalations import sweep_forever

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    sweeper = asyncio.create_task(sweep_forever(stop))
    try:
        yield
    finally:
        stop.set()
        await sweeper


app = FastAPI(
    title="Aval — kernel",
    version="1.1.0",
    description=(
        "Mandates, passkeys and escalations. Contracts: aval/contracts/api.yaml."
    ),
    lifespan=lifespan,
)

# The web console runs on its own origin (ADR-022), and passkeys are bound to
# a registrable domain (ADR-018) — so CORS is load-bearing, not boilerplate.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().rp_origin, "http://localhost:5173",
                   "http://app.localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mandates.router)
app.include_router(escalations.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "kernel"}
