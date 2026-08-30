"""Telegram channel for the /agent conversation."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from src.agent import telegram as tg
from src.agent.service import session_binding
from src.api.main import create_app


def _message(text: str, chat_id: int = 42, user_id: int = 7) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": "marta"},
        },
    }


def _callback(data: str, chat_id: int = 42, user_id: int = 7) -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "from": {"id": user_id, "username": "marta"},
            "message": {
                "message_id": 11,
                "text": "Pending…",
                "chat": {"id": chat_id, "type": "private"},
            },
        },
    }


def test_parse_message_and_session_id():
    inbound = tg.parse_update(_message("quiero una pizza"))
    assert inbound is not None
    assert inbound.kind == "message"
    assert inbound.text == "quiero una pizza"
    assert tg.session_id_for(inbound.chat_id) == "tg:42"


def test_parse_command_strips_bot_mention():
    inbound = tg.parse_update(_message("/start@AvalBot"))
    assert inbound is not None
    assert inbound.kind == "command"
    assert inbound.command == "start"


def test_parse_callback_approve():
    inbound = tg.parse_update(_callback("approve"))
    assert inbound is not None
    assert inbound.kind == "callback"
    assert inbound.text == "approve"
    assert inbound.callback_query_id == "cb-1"


def test_parse_ignores_empty_update():
    assert tg.parse_update({}) is None
    assert tg.parse_update({"message": {"chat": {"id": 1}, "text": "  "}}) is None


def test_split_images_and_keyboard():
    body, photos = tg.split_images(
        ["Pizza napolitana at 18000 COP", "Image: https://cdn.example/p.jpg"]
    )
    assert "Pizza napolitana" in body
    assert photos == ["https://cdn.example/p.jpg"]
    inbound = tg.parse_update(_message("x"))
    assert inbound is not None
    out = tg.outbound_from_turn(inbound, {"replies": ["This needs you."], "awaiting_human": True})
    assert out[0].reply_markup == tg.APPROVE_KEYBOARD


def test_secret_matches_fail_closed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert tg.secret_matches("s3cret") is True
    assert tg.secret_matches("nope") is False
    assert tg.secret_matches(None) is False
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    # module-level fallback may still hold .env; compare against empty expected
    monkeypatch.setattr(tg, "TELEGRAM_WEBHOOK_SECRET", "")
    assert tg.secret_matches("anything") is True


def test_start_does_not_call_agent(monkeypatch):
    called = []
    monkeypatch.setattr(tg.service, "turn", lambda *a, **k: called.append(1))
    result = tg.handle_update(None, _message("/start"))
    assert called == []
    assert result.outbound[0].text == tg.START_TEXT


def test_handle_message_uses_telegram_session(monkeypatch):
    seen: dict = {}

    def fake_turn(conn, **kwargs):
        seen.update(kwargs)
        return {"replies": ["Bought: pizza."], "awaiting_human": False}

    monkeypatch.setattr(tg.service, "turn", fake_turn)
    result = tg.handle_update(object(), _message("quiero pizza"))
    assert seen["session_id"] == "tg:42"
    assert seen["channel"] == "telegram"
    assert seen["person"] == "marta"
    assert result.outbound[0].text == "Bought: pizza."
    assert result.outbound[0].reply_markup is None


def test_callback_mutates_pending_and_answers(monkeypatch):
    monkeypatch.setattr(
        tg.service,
        "turn",
        lambda *a, **k: {"replies": ["Approved. Re-running."], "awaiting_human": False},
    )
    result = tg.handle_update(object(), _callback("approve"))
    kinds = [item.kind for item in result.outbound]
    assert kinds[0] == "answer_callback"
    assert kinds[1] == "edit"
    assert "APROBADO" in result.outbound[1].text
    assert result.outbound[-1].text == "Approved. Re-running."


def test_session_binding_finds_live_run():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE agent_runs ("
        "run_id TEXT, agent_id TEXT, mandate_jti TEXT, session_id TEXT, "
        "status TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO agent_runs(run_id, agent_id, mandate_jti, session_id, status, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        ("run-1", "agt_flights", "mandate-agent-1", "tg:42", "awaiting_human", "2026-08-30"),
    )
    bound = session_binding(conn, "tg:42")
    assert bound == {"agent_id": "agt_flights", "mandate_jti": "mandate-agent-1"}
    assert session_binding(conn, "tg:99") is None


@pytest.mark.asyncio
async def test_webhook_always_200_when_unconfigured(monkeypatch):
    monkeypatch.setattr(tg, "configured", lambda: False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/bot/telegram", json={"update_id": 1})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_webhook_wrong_secret_does_not_run_agent(monkeypatch):
    monkeypatch.setattr(tg, "configured", lambda: True)
    monkeypatch.setattr(tg, "secret_matches", lambda header: False)
    mocked = AsyncMock()
    monkeypatch.setattr("src.api.routers.bot.tg.handle_update", mocked)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/bot/telegram",
            json=_message("quiero pizza"),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
    assert resp.status_code == 200
    mocked.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_delivers_turn(monkeypatch):
    monkeypatch.setattr(tg, "configured", lambda: True)
    monkeypatch.setattr(tg, "secret_matches", lambda header: True)
    inbound = tg.parse_update(_message("quiero pizza"))
    handled = tg.HandleResult(
        inbound=inbound,
        outbound=[tg.Outbound(kind="send", chat_id=42, text="ok")],
    )
    monkeypatch.setattr("src.api.routers.bot.tg.handle_update", lambda conn, u: handled)
    deliver = AsyncMock()
    monkeypatch.setattr("src.api.routers.bot.tg.deliver", deliver)
    monkeypatch.setattr("src.api.routers.bot.deps.agent_conn", lambda: object())
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/bot/telegram", json=_message("quiero pizza"))
    assert resp.status_code == 200
    deliver.assert_awaited()
