"""Explicit catalogue searches stop before the deterministic purchase gate."""

from __future__ import annotations

from src.agent import graph, router


def test_explicit_search_is_read_only_but_buy_language_wins() -> None:
    assert graph.request_mode("Busca agua en Rappi y muéstrame opciones") == "search"
    assert graph.request_mode("Busca agua en Rappi y cómprame una") == "purchase"
    assert graph.request_mode("Quiero una botella de agua") == "purchase"


def test_obvious_rappi_dispatch_does_not_wait_for_a_model(monkeypatch) -> None:
    class Result:
        @staticmethod
        def fetchall():
            return [
                {
                    "agent_id": "agt_rappi",
                    "agent_name": "rappi_comprador",
                    "mandate_jti": "mdt_rappi",
                    "claims": (
                        '{"currency":"COP","scope":{"categories":["groceries"],'
                        '"merchants":["rappi"]}}'
                    ),
                    "created_at": "2026-08-30T00:00:00Z",
                }
            ]

    class Conn:
        @staticmethod
        def execute(*_args):
            return Result()

    monkeypatch.setattr(
        router.llm,
        "parse_request",
        lambda _text: (_ for _ in ()).throw(AssertionError("dispatcher called the model")),
    )

    selected = router.select_agent(Conn(), "Busca una botella de agua en Rappi")

    assert selected is not None
    assert selected["agent_id"] == "agt_rappi"
    assert selected["category"] == "groceries"


def test_rappi_search_perception_does_not_wait_for_a_model(monkeypatch) -> None:
    class Result:
        @staticmethod
        def fetchone():
            return {"claims": '{"scope":{"merchants":["rappi"]},"currency":"COP"}'}

    class Conn:
        @staticmethod
        def execute(*_args):
            return Result()

    def model_must_not_run(*_args, **_kwargs):
        raise AssertionError("a direct Rappi search must not call the model")

    summary = {
        "purchases_made": 0,
        "total_spent": "0.00",
    }
    monkeypatch.setattr(graph.registry, "get_version", lambda *_args: {"ontology": "{}"})
    monkeypatch.setattr(graph.ontology_mod, "render", lambda _onto: "rappi")
    monkeypatch.setattr(graph.memory, "summarise", lambda *_args: summary)
    monkeypatch.setattr(graph.memory, "render", lambda _summary: "no purchases")
    monkeypatch.setattr(graph.limits, "guard_llm_call", model_must_not_run)
    monkeypatch.setattr(graph.llm, "parse_request", model_must_not_run)
    monkeypatch.setattr(graph, "_save", lambda _conn, current, **_kwargs: current)
    run = {
        "agent_id": "agt_rappi",
        "agent_version": 1,
        "mandate_jti": "mdt_rappi",
        "state": {
            "request": "Busca agua en Rappi",
            "request_mode": "search",
            "guidance": [],
        },
    }

    assert graph.node_perceive(Conn(), run) == "search"
    assert run["state"]["direct_read_only_search"] is True
    assert run["state"]["criteria"]["notes"] == "direct read-only merchant search"


def test_search_proposal_finishes_without_entering_gate(monkeypatch) -> None:
    offer = {
        "offer_id": "rappi_store_water",
        "merchant_id": "rappi",
        "title": "Agua Cristal 1 L",
        "price": "1725.00",
        "currency": "COP",
        "images": ["https://images.rappi.com/water.png"],
    }
    run = {
        "run_id": "run_search",
        "agent_id": "agt_rappi",
        "agent_version": 1,
        "mandate_jti": "mdt_rappi",
        "status": "running",
        "state": {
            "request": "Busca agua",
            "request_mode": "search",
            "direct_read_only_search": True,
            "offers": [offer],
        },
    }
    monkeypatch.setattr(
        graph.limits,
        "guard_llm_call",
        lambda *_args: (_ for _ in ()).throw(AssertionError("model budget touched")),
    )
    monkeypatch.setattr(
        graph.llm,
        "propose",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("model called")),
    )
    monkeypatch.setattr(graph, "_save", lambda _conn, current, **_kwargs: current)

    assert graph.node_propose(None, run) == "done"
    assert run["state"]["result"]["status"] == "proposed"
    assert run["state"]["proposal"]["title"] == "Agua Cristal 1 L"
    assert run["state"]["proposal"]["source"] == "deterministic"
