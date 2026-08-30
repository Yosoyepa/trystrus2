#!/usr/bin/env python
"""Seed the Rappi buyer agent with a dual-signed COP mandate.

Mirrors `src/agent/seed.py` issuance, scoped to the Rappi bridge merchant
(decision 0030): `token` is the agent lane's compact JWS (var/keys key);
`sd_jwt` is the kernel lane's SD-JWT (secrets key) so BOTH verifiers accept.

Run against the same database as the kernel:

    AVAL_DATABASE_URL=postgresql://aval:aval@localhost:5432/aval \
      uv run python scripts/seed-rappi-agent.py

It is idempotent: restarting the local bridge does not issue a second Rappi
mandate for the same demo agent.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# The container image sets PYTHONPATH for us; a host invocation through
# `uv run python scripts/seed-rappi-agent.py` does not. Keep the script's
# documented local command self-contained without affecting package imports.
ROOT = Path(__file__).resolve().parents[1]
for import_path in (str(ROOT), str(ROOT / "src")):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

SCOPE = {"categories": ["food", "groceries", "retail"], "merchants": ["rappi"]}
LIMITS = {
    "max_per_txn": "60000.00",
    "total_budget": "200000.00",
    "max_txn": {"count": 4, "period": "month"},
}


def main() -> int:
    # Imports depend on the local path setup above. Keeping them here means
    # the same file works from the repo root and in the container image.
    from src.agent import db, registry
    from src.agent import mandate as agent_mandate
    from src.agent.mocks.rail import vault_instrument
    from src.api.services.mandate_registry import MandateRegistry

    from trustlib.models import MandateClaims

    conn = db.init()
    existing = conn.execute(
        "SELECT id, owner_id FROM agents WHERE name=? LIMIT 1", ("rappi_comprador",)
    ).fetchone()
    if existing:
        agent_id = existing["id"]
        owner = existing["owner_id"]
        mandate = conn.execute(
            "SELECT jti FROM mandates WHERE agent_id=? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        if mandate:
            print(
                json.dumps(
                    {"agent_id": agent_id, "mandate_jti": mandate["jti"], "seeded": False}
                )
            )
            return 0
    else:
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
    issued = agent_mandate.issue(
        conn,
        user_id=owner,
        agent_id=agent_id,
        agent_jwk=json.loads(agent["public_jwk"]),
        payment_method_ref=token_ref,
        scope=SCOPE,
        conditions={
            "and": [
                {"<": [{"var": "offer.price"}, 60000]},
                {"in": [{"var": "offer.category"}, ["food", "groceries", "retail"]]},
            ]
        },
        limits=LIMITS,
        validity={
            "not_before": "2026-08-30T00:00:00Z",
            "expires_at": "2026-09-30T23:59:59Z",
            "exp": int(time.time()) + 30 * 24 * 3600,
        },
        currency="COP",
        signed_with="passkey(mock): challenge = canonical hash of the mandate",
    )
    # kernel lane: same claims, kernel issuer signature
    claims = MandateClaims.model_validate(issued["claims"])
    sd_jwt = MandateRegistry().sign(claims).sd_jwt
    conn.execute(
        "UPDATE mandates SET sd_jwt=? WHERE jti=?",
        (sd_jwt, issued["jti"]),
    )
    print(json.dumps({"agent_id": agent_id, "mandate_jti": issued["jti"], "seeded": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
