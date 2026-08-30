"""Demo data: the story from the architecture doc, ready to run.

Marta authorises flights to Cordoba under $150 until the end of the month, on a
vaulted card the agent never sees.
"""

from __future__ import annotations

import json
from typing import Any

from . import db, registry, watcher
from . import mandate as mandate_mod
from .config import ONTOLOGY_DIR
from .ids import now_ts
from .mocks import merchant, rail

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


# The API lazily installs the demo data for a completely empty database. A
# volume reset can, however, leave the two demo agents behind while losing
# every mandate row. Restrict recovery to these known demo identities: a
# user-created agent that is simply waiting for a human-issued mandate must
# remain fail-closed.
DEMO_AGENT_NAMES = ("flights_marta", "rappi_comprador")


def needs_demo_seed(conn) -> bool:
    """Return whether the local demo seed is absent or only partially present.

    Existing mandate rows are authority, including non-active rows. In
    particular, a revoked, suspended, or expired mandate must never trigger a
    replacement mandate. The only recoverable partial state is a recognised
    demo agent with *no* mandate rows at all, which is what a failed demo seed
    or an incomplete local volume reset leaves behind.
    """
    if conn.execute("SELECT 1 FROM agents LIMIT 1").fetchone() is None:
        return True
    if conn.execute("SELECT 1 FROM mandates LIMIT 1").fetchone() is not None:
        return False
    placeholders = ", ".join("?" for _ in DEMO_AGENT_NAMES)
    return (
        conn.execute(
            f"SELECT 1 FROM agents WHERE name IN ({placeholders}) LIMIT 1",
            DEMO_AGENT_NAMES,
        ).fetchone()
        is not None
    )


def seed_all(conn=None) -> dict[str, Any]:
    conn = conn or db.init()
    offers = merchant.seed(conn)

    marta = registry.add_person(conn, "Marta", "marta@example.com", "owner")
    sergio = registry.add_person(conn, "Sergio", "sergio@example.com", "auditor")

    agent_id = registry.create_agent(
        conn,
        "flights_marta",
        owner_id=marta,
        approver_id=marta,
        auditor_id=sergio,
        ontology=FLIGHTS_ONTOLOGY,
        model_cfg={"model": "gpt-4.1-nano", "role": "propose only"},
        actor=marta,
    )

    token_ref = rail.vault_instrument(conn, "pending", label="visa-4111")
    agent = registry.get_agent(conn, agent_id)
    import json as _json

    issued = mandate_mod.issue(
        conn,
        user_id=marta,
        agent_id=agent_id,
        agent_jwk=_json.loads(agent["public_jwk"]),
        payment_method_ref=token_ref,
        scope={"categories": ["flights"], "merchants": ["vuelaya"]},
        conditions={
            "and": [
                {"<": [{"var": "offer.price"}, 150]},
                {"==": [{"var": "offer.category"}, "flights"]},
            ]
        },
        limits={
            "max_per_txn": "150.00",
            "total_budget": "600.00",
            "max_txn": {"count": 3, "period": "month"},
        },
        validity={
            "not_before": "2026-09-01T00:00:00Z",
            "expires_at": "2026-09-30T23:59:59Z",
            "exp": now_ts() + 30 * 24 * 3600,
        },
        signed_with="passkey(mock): challenge = canonical hash of the mandate",
    )
    conn.execute(
        "UPDATE payment_instruments SET mandate_jti=? WHERE token_ref=?", (issued["jti"], token_ref)
    )

    watch = watcher.create_watch(
        conn,
        agent_id=agent_id,
        mandate_jti=issued["jti"],
        query={"destination": "COR", "category": "flights"},
        threshold={"<=": [{"var": "offer.price"}, 125]},
        interval_s=60,
        autobuy=True,
        created_by=marta,
    )

    ONTOLOGY_DIR.mkdir(exist_ok=True)
    import yaml

    (ONTOLOGY_DIR / "flights.yaml").write_text(
        yaml.safe_dump(FLIGHTS_ONTOLOGY, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    # A COP mandate for the real MCP merchants. Currencies are not converted
    # inside an enforcement path -- a silent conversion is a way to spend more
    # than the person agreed to -- so a COP merchant needs a COP mandate.
    cop_token = rail.vault_instrument(conn, "pending", label="visa-cop")
    cop = mandate_mod.issue(
        conn,
        user_id=marta,
        agent_id=agent_id,
        agent_jwk=_json.loads(agent["public_jwk"]),
        payment_method_ref=cop_token,
        scope={"categories": ["flights", "retail"], "merchants": ["vuelaya-mcp", "mami"]},
        conditions={"<": [{"var": "offer.price"}, 200000]},
        limits={
            "max_per_txn": "200000.00",
            "total_budget": "600000.00",
            "max_txn": {"count": 5, "period": "month"},
        },
        validity={
            "not_before": "2026-09-01T00:00:00Z",
            "expires_at": "2026-09-30T23:59:59Z",
            "exp": now_ts() + 30 * 24 * 3600,
        },
        currency="COP",
        signed_with="passkey(mock): challenge = canonical hash of the mandate",
    )
    conn.execute(
        "UPDATE payment_instruments SET mandate_jti=? WHERE token_ref=?", (cop["jti"], cop_token)
    )

    try:
        rappi = seed_rappi_buyer(conn)
    except Exception as exc:  # the flights demo must not need the rappi keys
        rappi = {"skipped": str(exc)[:160]}

    return {
        "people": {"marta": marta, "sergio": sergio},
        "agent_id": agent_id,
        "mandate_cop": cop["jti"],
        "mandate_jti": issued["jti"],
        "payment_token": token_ref,
        "offers": offers,
        "watch_id": watch["watch_id"],
        "rappi_buyer": rappi,
    }


def seed_rappi_buyer(conn=None) -> dict[str, Any]:
    """The Rappi buyer: `scripts/seed-rappi-agent.py` as a function.

    Lives in the standard seed so a DB reset brings the rappi agent back
    together with the flights demo instead of losing it until someone
    remembers the script. The kernel-lane SD-JWT needs the kernel signing
    key; where that is unavailable the caller's guard skips this with a
    note instead of failing the rest of the seed.
    """
    from .mocks.rail import vault_instrument

    conn = conn or db.init()
    owner = registry.add_person(conn, "Rappi Owner", "rappi@example.com", "owner")
    agent_id = registry.create_agent(
        conn,
        "rappi_comprador",
        owner_id=owner,
        approver_id=owner,
        ontology={
            "role": "comprador Rappi bajo mandato",
            "categories": ["food", "groceries", "retail"],
        },
        model_cfg={"model": "propose only"},
        actor=owner,
    )
    token_ref = vault_instrument(conn, "pending", label="rappi-saved-card")
    agent = registry.get_agent(conn, agent_id)
    issued = mandate_mod.issue(
        conn,
        user_id=owner,
        agent_id=agent_id,
        agent_jwk=json.loads(agent["public_jwk"]),
        payment_method_ref=token_ref,
        scope={"categories": ["food", "groceries", "retail"], "merchants": ["rappi"]},
        conditions={
            "and": [
                {"<": [{"var": "offer.price"}, 60000]},
                {"in": [{"var": "offer.category"}, ["food", "groceries", "retail"]]},
            ]
        },
        limits={
            "max_per_txn": "60000.00",
            "total_budget": "200000.00",
            "max_txn": {"count": 4, "period": "month"},
        },
        validity={
            "not_before": "2026-08-30T00:00:00Z",
            "expires_at": "2026-09-30T23:59:59Z",
            "exp": now_ts() + 30 * 24 * 3600,
        },
        currency="COP",
        signed_with="passkey(mock): challenge = canonical hash of the mandate",
    )
    # kernel lane: same claims, kernel issuer signature
    from src.api.services.mandate_registry import MandateRegistry

    from trustlib.models import MandateClaims

    claims = MandateClaims.model_validate(issued["claims"])
    sd_jwt = MandateRegistry().sign(claims).sd_jwt
    conn.execute("UPDATE mandates SET sd_jwt=? WHERE jti=?", (sd_jwt, issued["jti"]))
    return {"agent_id": agent_id, "mandate_jti": issued["jti"]}
