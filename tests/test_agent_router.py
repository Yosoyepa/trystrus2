"""Dispatcher scoring: deterministic agent/mandate selection."""

from src.agent.router import score_candidates, select_agent

FLIGHTS = {
    "agent_id": "agt_a",
    "agent_name": "flights_marta",
    "mandate_jti": "mdt_a",
    "created_at": "2026-08-30T12:00:00+00:00",
    "scope": {"categories": ["flights", "hotels"], "merchants": ["vuelaya-mcp"]},
    "currency": "USD",
}
RAPPI = {
    "agent_id": "agt_b",
    "agent_name": "rappi_comprador",
    "mandate_jti": "mdt_b",
    "created_at": "2026-08-30T12:00:00+00:00",
    "scope": {
        "categories": ["food", "groceries", "retail"],
        "merchants": ["rappi"],
    },
    "currency": "COP",
}


class _NoCandidateConn:
    def execute(self, _sql: str):
        return self

    def fetchall(self) -> list[dict]:
        return []


def test_rappi_request_prefers_rappi_agent() -> None:
    scored = score_candidates(
        [FLIGHTS, RAPPI],
        {"category": "groceries"},
        "quiero unas papas pringles en rappi para la oficina",
    )
    assert scored[0]["mandate_jti"] == "mdt_b"
    assert scored[0]["score"] > scored[1]["score"]


def test_flight_request_prefers_flights_agent() -> None:
    scored = score_candidates(
        [FLIGHTS, RAPPI],
        {"category": "flights"},
        "buscame un vuelo de BOG a COR",
    )
    assert scored[0]["mandate_jti"] == "mdt_a"
    assert scored[0]["score"] > scored[1]["score"]


def test_merchant_word_breaks_ties() -> None:
    scored = score_candidates(
        [FLIGHTS, RAPPI],
        {"category": None},
        "algo para comer, pide por rappi",
    )
    assert scored[0]["mandate_jti"] == "mdt_b"


def test_parse_fallback_water_is_groceries() -> None:
    from src.agent.llm import _parse_fallback

    assert _parse_fallback("buscame una botella de agua")["category"] == "groceries"
    assert _parse_fallback("vuelo a cordoba")["category"] == "flights"
    from src.agent.router import inferred_category

    assert inferred_category("buscame una botella de agua") == "groceries"


def test_water_request_prefers_rappi_even_if_parser_says_flights() -> None:
    from src.agent.router import inferred_category

    criteria = {"category": inferred_category("buscame una botella de agua")}
    ranked = score_candidates([FLIGHTS, RAPPI], criteria, "buscame una botella de agua")
    assert ranked[0]["mandate_jti"] == "mdt_b"
    assert ranked[0]["score"] > ranked[1]["score"]


def test_no_match_scores_zero_everywhere() -> None:
    scored = score_candidates(
        [FLIGHTS, RAPPI],
        {"category": "space travel"},
        "quiero ir a la luna",
    )
    assert all(item["score"] == 0 for item in scored)


def test_no_candidate_returns_before_calling_the_model(monkeypatch) -> None:
    def model_must_not_run(_text: str) -> dict:
        raise AssertionError("no active mandate should short-circuit before the LLM")

    monkeypatch.setattr("src.agent.router.llm.parse_request", model_must_not_run)

    assert select_agent(_NoCandidateConn(), "busca una botella de agua") is None
