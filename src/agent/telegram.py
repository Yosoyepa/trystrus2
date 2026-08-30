"""Telegram as a buyer channel for the agent conversation (decision #13).

The gate is still the only path to money. This module is a renderer and a
router: it maps a Telegram chat onto a `service.turn`, puts Approve/Reject on
an inline keyboard when the run parks, and mutates the pending message when
the human answers. Silence still denies at 120 s.

HTTP lives in `src/api/routers/bot.py` (`POST /bot/telegram`). Locally, set
`TELEGRAM_MODE=polling` so the kernel long-polls without a public HTTPS URL.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import service
from .config import (
    ESCALATION_TIMEOUT_S,
    PRODUCT_NAME,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_MODE,
    TELEGRAM_WEBHOOK_SECRET,
    TELEGRAM_WEBHOOK_URL,
)

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
IMAGE_LINE = re.compile(r"^Image:\s+(\S+)\s*$", re.MULTILINE)
BOT_COMMAND = re.compile(r"^/([A-Za-z0-9_]+)(?:@\S+)?(?:\s+(.*))?$", re.DOTALL)

START_TEXT = (
    f"Soy el agente de compras de {PRODUCT_NAME}.\n\n"
    "Escribime qué querés (un vuelo, un hotel, comida, mercado) y lo busco "
    "dentro del mandato que firmaste. Si me paso del límite te pido aprobación "
    f"acá — si no contestás en {ESCALATION_TIMEOUT_S} s, rechazo. El silencio "
    "nunca aprueba.\n\n"
    "Comandos: /help · /status"
)
HELP_TEXT = (
    "Mandame un pedido en lenguaje natural, igual que en la consola.\n\n"
    "• «quiero una pizza» / «vuelo a Córdoba bajo 150»\n"
    "• Si el monto se sale del mandato, te llegan botones Aprobar / Rechazar.\n"
    "• «approve» o «sí» también sirven; «reject» o «no» rechazan.\n"
    "• La revocación del mandato es con passkey en la consola, no desde acá."
)
REVOKE_TEXT = (
    "La revocación se hace en la consola con passkey (WebAuthn). "
    "Desde Telegram no puedo matar el mandato — eso es a propósito."
)
FAIL_TEXT = "Algo falló de mi lado. No se cobró nada."

APPROVE_KEYBOARD: dict[str, Any] = {
    "inline_keyboard": [
        [
            {"text": "✅ Aprobar", "callback_data": "approve"},
            {"text": "❌ Rechazar", "callback_data": "reject"},
        ]
    ]
}


def bot_token() -> str:
    return (
        os.environ.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("AVAL_TELEGRAM_BOT_TOKEN")
        or TELEGRAM_BOT_TOKEN
        or ""
    )


def webhook_secret() -> str:
    return (
        os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        or os.environ.get("AVAL_TELEGRAM_WEBHOOK_SECRET")
        or TELEGRAM_WEBHOOK_SECRET
        or ""
    )


def webhook_url() -> str:
    return os.environ.get("TELEGRAM_WEBHOOK_URL") or TELEGRAM_WEBHOOK_URL or ""


def configured() -> bool:
    return bool(bot_token())


def polling_enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    mode = os.environ.get("TELEGRAM_MODE", TELEGRAM_MODE).strip().lower()
    return bool(bot_token()) and mode == "polling"


def secret_matches(header_value: str | None) -> bool:
    """Fail closed: a configured secret with a missing/wrong header is a no."""
    expected = webhook_secret()
    if not expected:
        return True
    got = header_value or ""
    try:
        return hmac.compare_digest(got, expected)
    except TypeError:
        return False


def session_id_for(chat_id: int | str) -> str:
    return f"tg:{chat_id}"


@dataclass
class Inbound:
    chat_id: int
    user_id: int
    text: str
    kind: str  # message | callback | command
    command: str | None = None
    message_id: int | None = None
    callback_query_id: str | None = None
    original_text: str | None = None
    username: str | None = None


@dataclass
class Outbound:
    kind: str  # send | photo | edit | answer_callback
    chat_id: int
    text: str = ""
    photo_url: str | None = None
    reply_markup: dict[str, Any] | None = None
    message_id: int | None = None
    callback_query_id: str | None = None


@dataclass
class HandleResult:
    inbound: Inbound | None
    outbound: list[Outbound] = field(default_factory=list)
    turn: dict[str, Any] | None = None


def parse_update(update: dict[str, Any]) -> Inbound | None:
    """Telegram Update JSON → Inbound. Pure; nothing is sent or charged."""
    if not isinstance(update, dict):
        return None
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        message = callback.get("message") or {}
        from_user = callback.get("from") or {}
        chat = (message.get("chat") or {}) if isinstance(message, dict) else {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None
        data = (callback.get("data") or "").strip()
        return Inbound(
            chat_id=int(chat_id),
            user_id=int(from_user.get("id") or 0),
            text=data,
            kind="callback",
            message_id=message.get("message_id") if isinstance(message, dict) else None,
            callback_query_id=str(callback.get("id") or "") or None,
            original_text=message.get("text") if isinstance(message, dict) else None,
            username=from_user.get("username"),
        )

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        return None
    match = BOT_COMMAND.match(text)
    if match:
        return Inbound(
            chat_id=int(chat_id),
            user_id=int(from_user.get("id") or 0),
            text=(match.group(2) or "").strip(),
            kind="command",
            command=match.group(1).lower(),
            message_id=message.get("message_id"),
            username=from_user.get("username"),
        )
    return Inbound(
        chat_id=int(chat_id),
        user_id=int(from_user.get("id") or 0),
        text=text,
        kind="message",
        message_id=message.get("message_id"),
        username=from_user.get("username"),
    )


def split_images(replies: list[str]) -> tuple[str, list[str]]:
    photos: list[str] = []
    kept: list[str] = []
    for line in replies:
        photos.extend(IMAGE_LINE.findall(line))
        cleaned = IMAGE_LINE.sub("", line).strip()
        if cleaned:
            kept.append(cleaned)
    return "\n\n".join(kept), photos


def command_reply(inbound: Inbound) -> str | None:
    if inbound.kind != "command":
        return None
    if inbound.command in {"start", "help", "ayuda"}:
        return START_TEXT if inbound.command == "start" else HELP_TEXT
    if inbound.command == "revoke":
        return REVOKE_TEXT
    return None


def outbound_from_turn(
    inbound: Inbound,
    result: dict[str, Any],
    *,
    pending_edit: str | None = None,
) -> list[Outbound]:
    items: list[Outbound] = []
    if inbound.kind == "callback" and inbound.callback_query_id:
        lowered = inbound.text.lower()
        if lowered in {"approve", "approved", "yes", "si", "sí", "ok"}:
            toast = "Aprobado — el gate vuelve a chequear antes de cobrar"
        else:
            toast = "Rechazado. No se cobró nada"
        items.append(
            Outbound(
                kind="answer_callback",
                chat_id=inbound.chat_id,
                text=toast,
                callback_query_id=inbound.callback_query_id,
            )
        )
        if inbound.message_id is not None and pending_edit:
            items.append(
                Outbound(
                    kind="edit",
                    chat_id=inbound.chat_id,
                    text=pending_edit,
                    message_id=inbound.message_id,
                    reply_markup={"inline_keyboard": []},
                )
            )

    replies = list(result.get("replies") or [])
    body, photos = split_images(replies)
    if not body:
        body = "…"
    markup = APPROVE_KEYBOARD if result.get("awaiting_human") else None
    if photos:
        items.append(
            Outbound(
                kind="photo",
                chat_id=inbound.chat_id,
                text=body[:1024],
                photo_url=photos[0],
                reply_markup=markup if len(body) <= 1024 else None,
            )
        )
        for extra in photos[1:]:
            items.append(Outbound(kind="photo", chat_id=inbound.chat_id, photo_url=extra))
        if len(body) > 1024:
            items.append(
                Outbound(
                    kind="send",
                    chat_id=inbound.chat_id,
                    text=body,
                    reply_markup=markup,
                )
            )
        return items
    items.append(
        Outbound(
            kind="send",
            chat_id=inbound.chat_id,
            text=body,
            reply_markup=markup,
        )
    )
    return items


def pending_edit_text(inbound: Inbound) -> str | None:
    if not inbound.original_text:
        return None
    approved = inbound.text.lower() in {"approve", "approved", "yes", "si", "sí", "ok"}
    stamp = "✅ APROBADO" if approved else "❌ RECHAZADO"
    who = inbound.username or f"tg:{inbound.user_id}"
    return f"{inbound.original_text}\n\n{stamp} por {who}"


def handle_update(conn, update: dict[str, Any]) -> HandleResult:
    """Parse + run a turn. Does not talk to Telegram — the caller delivers."""
    inbound = parse_update(update)
    if inbound is None:
        return HandleResult(inbound=None)

    if inbound.kind == "command":
        if inbound.command == "status":
            text = _status_text(conn, inbound)
        else:
            text = command_reply(inbound) or HELP_TEXT
        return HandleResult(
            inbound=inbound,
            outbound=[Outbound(kind="send", chat_id=inbound.chat_id, text=text)],
        )

    person = inbound.username or f"tg:{inbound.user_id}"
    try:
        result = service.turn(
            conn,
            text=inbound.text,
            session_id=session_id_for(inbound.chat_id),
            person=person,
            channel="telegram",
        )
    except Exception:
        log.exception("telegram turn failed")
        return HandleResult(
            inbound=inbound,
            outbound=[Outbound(kind="send", chat_id=inbound.chat_id, text=FAIL_TEXT)],
        )
    return HandleResult(
        inbound=inbound,
        outbound=outbound_from_turn(inbound, result, pending_edit=pending_edit_text(inbound)),
        turn=result,
    )


def _status_text(conn, inbound: Inbound) -> str:
    session_id = session_id_for(inbound.chat_id)
    bound = service.session_binding(conn, session_id)
    if bound is None:
        row = conn.execute(
            "SELECT agent_id, mandate_jti FROM agent_runs WHERE session_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row:
            bound = {"agent_id": row["agent_id"], "mandate_jti": row["mandate_jti"]}
    if bound is None:
        return "Todavía no hay una compra en este chat. Mandame un pedido."
    try:
        view = service.mandate_view(conn, bound["mandate_jti"])
    except Exception:
        return f"Agente {bound['agent_id']} · mandato {bound['mandate_jti']}"
    claims = view.get("claims") or {}
    limits = claims.get("limits") or {}
    return (
        f"Agente {bound['agent_id']}\n"
        f"Mandato {view['jti']} · {view['status']}\n"
        f"Gastado {view['spent']} · reservado {view['reserved']}\n"
        f"Tope por compra: {limits.get('max_per_txn', '—')}"
    )


class TelegramClient:
    """Thin Bot API client. Token is never logged."""

    def __init__(self, token: str | None = None, *, client: httpx.AsyncClient | None = None):
        self._token = token or bot_token()
        self._owned = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(35.0))

    @property
    def base(self) -> str:
        return f"{API_ROOT}/bot{self._token}"

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()

    async def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(f"{self.base}/{method}", json=payload)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method} failed")
        return body

    async def get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, Any]]:
        response = await self._client.post(
            f"{self.base}/getUpdates",
            json={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=timeout + 10,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            return []
        return list(body.get("result") or [])

    async def delete_webhook(self) -> None:
        await self._post("deleteWebhook", {"drop_pending_updates": False})

    async def set_webhook(self, url: str, secret: str) -> None:
        payload: dict[str, Any] = {
            "url": url,
            "allowed_updates": ["message", "callback_query"],
        }
        if secret:
            payload["secret_token"] = secret
        await self._post("setWebhook", payload)

    async def deliver(self, items: list[Outbound]) -> None:
        for item in items:
            if item.kind == "answer_callback" and item.callback_query_id:
                await self._post(
                    "answerCallbackQuery",
                    {"callback_query_id": item.callback_query_id, "text": item.text[:200]},
                )
            elif item.kind == "edit" and item.message_id is not None:
                payload: dict[str, Any] = {
                    "chat_id": item.chat_id,
                    "message_id": item.message_id,
                    "text": item.text[:4096],
                }
                if item.reply_markup is not None:
                    payload["reply_markup"] = item.reply_markup
                await self._post("editMessageText", payload)
            elif item.kind == "photo" and item.photo_url:
                payload = {
                    "chat_id": item.chat_id,
                    "photo": item.photo_url,
                    "caption": item.text[:1024],
                }
                if item.reply_markup:
                    payload["reply_markup"] = item.reply_markup
                await self._post("sendPhoto", payload)
            else:
                payload = {"chat_id": item.chat_id, "text": item.text[:4096]}
                if item.reply_markup:
                    payload["reply_markup"] = item.reply_markup
                await self._post("sendMessage", payload)


async def deliver(items: list[Outbound], *, client: TelegramClient | None = None) -> None:
    if not items or not bot_token():
        return
    owned = client is None
    api = client or TelegramClient()
    try:
        await api.deliver(items)
    finally:
        if owned:
            await api.aclose()


async def poll_forever(
    stop: asyncio.Event,
    *,
    conn_factory,
    token: str | None = None,
) -> None:
    """Long-poll getUpdates until `stop`. Used when TELEGRAM_MODE=polling."""
    token = token or bot_token()
    if not token:
        return
    api = TelegramClient(token)
    try:
        try:
            await api.delete_webhook()
        except Exception:
            log.warning("telegram: could not deleteWebhook before polling")
        offset = 0
        log.info("telegram: polling for agent turns")
        while not stop.is_set():
            try:
                updates = await api.get_updates(offset, timeout=25)
            except Exception:
                log.exception("telegram getUpdates failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    pass
                continue
            for update in updates:
                offset = int(update.get("update_id") or 0) + 1
                try:
                    result = handle_update(conn_factory(), update)
                    await api.deliver(result.outbound)
                except Exception:
                    log.exception("telegram update %s failed", update.get("update_id"))
    finally:
        await api.aclose()


async def install_webhook() -> None:
    url = webhook_url()
    token = bot_token()
    if not token or not url:
        return
    api = TelegramClient(token)
    try:
        await api.set_webhook(url, webhook_secret())
        log.info("telegram: webhook set")
    finally:
        await api.aclose()
