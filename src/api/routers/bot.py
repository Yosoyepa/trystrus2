"""Telegram webhook — contract `POST /bot/telegram`.

Telegram retries on anything other than 200, so this always returns 200.
A missing or wrong secret token is fail-closed: we acknowledge and do not
run the agent. The conversation itself is `src.agent.telegram`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from src.agent import telegram as tg

from .. import deps

log = logging.getLogger(__name__)

router = APIRouter(prefix="/bot", tags=["escalations"])


@router.post("/telegram")
async def telegram_webhook(request: Request) -> dict[str, Any]:
    if not tg.configured():
        return {"ok": True}
    if not tg.secret_matches(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        return {"ok": True}
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}
    if not isinstance(update, dict):
        return {"ok": True}
    conn = deps.agent_conn()
    if conn is None:
        log.warning("telegram webhook: agent storage unavailable")
        return {"ok": True}
    try:
        result = tg.handle_update(conn, update)
        await tg.deliver(result.outbound)
    except Exception:
        log.exception("telegram webhook failed")
    return {"ok": True}
