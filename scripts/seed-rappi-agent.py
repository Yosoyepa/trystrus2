#!/usr/bin/env python
"""Seed the Rappi buyer agent with a dual-signed COP mandate.

Thin wrapper over `src.agent.seed.seed_rappi_buyer` (also part of the
standard seed now, so a DB reset restores it automatically). `token` is the
agent lane's compact JWS (var/keys key); `sd_jwt` is the kernel lane's
SD-JWT (secrets key) so BOTH verifiers accept.

Run against the same database as the kernel:

    AVAL_DATABASE_URL=postgresql://aval:aval@localhost:5432/aval \
      uv run python scripts/seed-rappi-agent.py

It is idempotent: restarting the local bridge does not issue a second Rappi
mandate for the same demo agent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The container image sets PYTHONPATH for us; a host invocation through
# `uv run python scripts/seed-rappi-agent.py` does not. Keep the script's
# documented local command self-contained without affecting package imports.
ROOT = Path(__file__).resolve().parents[1]
for import_path in (str(ROOT), str(ROOT / "src")):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)


def main() -> int:
    from src.agent import db
    from src.agent.seed import seed_rappi_buyer

    conn = db.init()
    existing = conn.execute(
        "SELECT id FROM agents WHERE name=? LIMIT 1", ("rappi_comprador",)
    ).fetchone()
    if existing:
        mandate = conn.execute(
            "SELECT jti FROM mandates WHERE agent_id=? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1",
            (existing["id"],),
        ).fetchone()
        if mandate:
            print(
                json.dumps(
                    {
                        "agent_id": existing["id"],
                        "mandate_jti": mandate["jti"],
                        "seeded": False,
                    }
                )
            )
            return 0

    result = seed_rappi_buyer(conn)
    print(json.dumps({**result, "seeded": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
