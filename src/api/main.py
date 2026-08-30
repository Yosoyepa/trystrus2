"""The kernel service — `api` in the plans, `src/api/` here.

Hosts routers from two workstreams in separate folders with no cross-imports
outside `trustlib` (decision #19): identity and escalations are Dev 3's,
verify/purchases/audit/events are Dev 2's, and Dev 4's Telegram webhook mounts
here too.

Run: `uv run uvicorn api.main:app --app-dir src --reload --port 8001`
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, settings
from .routers import (
    agent_bridge,
    audit,
    decision,
    escalations,
    evidence,
    mandates,
)
from .services.escalations import sweep_forever

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop = asyncio.Event()
    from src.agent import service as agent_service

    try:
        merchants = agent_service.bootstrap()
        print(f"merchants registered: {sorted(merchants['merchants'])}")
    except Exception as exc:  # a broken merchant never blocks the kernel
        print(f"merchant bootstrap skipped: {exc}")
    sweeper = asyncio.create_task(sweep_forever(stop))
    try:
        yield
    finally:
        stop.set()
        await sweeper


def create_app(custom_settings: Settings | None = None, service: object | None = None) -> FastAPI:
    """Create an app with injectable settings and service."""
    cfg = custom_settings or settings()

    application = FastAPI(
        title="Aval — kernel",
        version="1.1.0",
        description=(
            "Mandates, passkeys, escalations, policy gate and audit. "
            "Contracts: aval/contracts/api.yaml."
        ),
        lifespan=lifespan,
    )

    application.state.settings = cfg
    if service is not None:
        application.state.service = service

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[cfg.rp_origin, "http://localhost:5173", "http://app.localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(mandates.router)
    application.include_router(escalations.router)
    application.include_router(decision.router)
    application.include_router(audit.router)
    application.include_router(evidence.router)
    application.include_router(agent_bridge.router)

    @application.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "service": "kernel"}

    @application.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
