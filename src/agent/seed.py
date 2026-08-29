"""Demo data: the story from the architecture doc, ready to run.

Marta authorises flights to Cordoba under $150 until the end of the month, on a
vaulted card the agent never sees.
"""
from __future__ import annotations
from typing import Any

from . import db, mandate as mandate_mod, registry, watcher
from .config import ONTOLOGY_DIR
from .ids import now_ts
from .mocks import merchant, rail
from .ontology import load_file

FLIGHTS_ONTOLOGY = {
    "domain": "flights",
    "what_the_buyer_cares_about": [
        "total price matters more than airline",
        "a direct flight is worth about 15 USD more than a one-stop",
        "red-eye departures are usually the cheapest fare of the day",
    ],
    "normal_ranges": {"BOG-COR": "110-145 USD", "BOG-MDE": "80-110 USD"},
    "vocabulary": {
        "red-eye": "a flight departing late at night and arriving early morning",
        "layover": "an intermediate stop where the passenger changes aircraft",
    },
    "when_unsure": "propose the cheapest option that meets the buyer's stated need",
}


def seed_all(conn=None) -> dict[str, Any]:
    conn = conn or db.init()
    offers = merchant.seed(conn)

    marta = registry.add_person(conn, "Marta", "marta@example.com", "owner")
    sergio = registry.add_person(conn, "Sergio", "sergio@example.com", "auditor")

    agent_id = registry.create_agent(
        conn, "flights_marta", owner_id=marta, approver_id=marta, auditor_id=sergio,
        ontology=FLIGHTS_ONTOLOGY,
        model_cfg={"model": "gpt-4.1-nano", "role": "propose only"}, actor=marta)

    token_ref = rail.vault_instrument(conn, "pending", label="visa-4111")
    agent = registry.get_agent(conn, agent_id)
    import json as _json
    issued = mandate_mod.issue(
        conn,
        user_id=marta, agent_id=agent_id, agent_jwk=_json.loads(agent["public_jwk"]),
        payment_method_ref=token_ref,
        scope={"categories": ["flights"], "merchants": ["vuelaya"]},
        conditions={"and": [
            {"<": [{"var": "offer.price"}, 150]},
            {"==": [{"var": "offer.category"}, "flights"]},
        ]},
        limits={"max_per_txn": "150.00", "total_budget": "600.00",
                "max_txn": {"count": 3, "period": "month"}},
        validity={"not_before": "2026-09-01T00:00:00Z",
                  "expires_at": "2026-09-30T23:59:59Z",
                  "exp": now_ts() + 30 * 24 * 3600},
        signed_with="passkey(mock): challenge = canonical hash of the mandate")
    conn.execute("UPDATE payment_instruments SET mandate_jti=? WHERE token_ref=?",
                 (issued["jti"], token_ref))

    watch = watcher.create_watch(
        conn, agent_id=agent_id, mandate_jti=issued["jti"],
        query={"destination": "COR", "category": "flights"},
        threshold={"<=": [{"var": "offer.price"}, 125]},
        interval_s=60, autobuy=True, created_by=marta)

    ONTOLOGY_DIR.mkdir(exist_ok=True)
    import yaml
    (ONTOLOGY_DIR / "flights.yaml").write_text(
        yaml.safe_dump(FLIGHTS_ONTOLOGY, sort_keys=False, allow_unicode=True),
        encoding="utf-8")

    return {"people": {"marta": marta, "sergio": sergio}, "agent_id": agent_id,
            "mandate_jti": issued["jti"], "payment_token": token_ref,
            "offers": offers, "watch_id": watch["watch_id"]}
