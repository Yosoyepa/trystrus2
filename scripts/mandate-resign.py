#!/usr/bin/env python
"""Re-sign seeded mandates with the kernel's issuer key.

Cross-lane key drift recovery: the agent seed signs mandates with
`var/keys/issuer.pem` while the kernel verifies against
`secrets/issuer_ed25519.pem` (two `load_or_create` call sites, two keys).
Until the lanes share one issuer key (team decision), this re-signs every
mandate IN PLACE — same jti, same claims, kernel signature — so the gate
stops rejecting `INVALID_SIGNATURE` on fixtures.

Run inside the kernel container:

    podman exec -i aval-kernel python - < scripts/mandate-resign.py
"""

from __future__ import annotations

import json
import sys

from src.agent import db
from src.agent import mandate as agent_mandate
from src.agent.crypto import jws as agent_jws
from src.agent.crypto.keys import load_or_create
from src.api.services.mandate_registry import MandateRegistry

from trustlib.models import MandateClaims


def main() -> int:
    conn = db.init()
    registry = MandateRegistry()
    rows = conn.execute("SELECT jti, claims, status FROM mandates").fetchall()
    resigned = 0
    for row in rows:
        raw_claims = row["claims"]
        if isinstance(raw_claims, str):
            raw_claims = json.loads(raw_claims)
        claims = MandateClaims.model_validate(raw_claims)
        issued = registry.sign(claims)  # kernel lane: SD-JWT under secrets key
        token = agent_jws.sign_compact(
            raw_claims,
            load_or_create(agent_mandate.ISSUER_KEY_NAME),
            kid=agent_mandate.KID,
            typ="mandate+jwt",
        )  # agent lane: compact JWS under var/keys key
        conn.execute(
            "UPDATE mandates SET sd_jwt=?, token=?, claims=? WHERE jti=?",
            (
                issued.sd_jwt,
                token,
                json.dumps(raw_claims, ensure_ascii=False),
                row["jti"],
            ),
        )
        resigned += 1
        print(f"re-signed {row['jti']} ({row['status']})")
    print(f"done: {resigned} mandates re-signed (sd_jwt=kernel, token=agent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
