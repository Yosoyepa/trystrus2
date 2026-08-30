"""Dispatcher scoring: deterministic agent/mandate selection."""

from src.agent.router import score_candidates

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


def test_no_match_scores_zero_everywhere() -> None:
    scored = score_candidates(
        [FLIGHTS, RAPPI],
        {"category": "space travel"},
        "quiero ir a la luna",
    )
    assert all(item["score"] == 0 for item in scored)
