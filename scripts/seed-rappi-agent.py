#!/usr/bin/env python
"""Seed the Rappi buyer agent with a dual-signed COP mandate.

Thin wrapper over `src.agent.seed.seed_rappi_buyer` (also part of the
standard seed now, so a DB reset restores it automatically). Run inside
the kernel container:

    podman exec -i aval-kernel python - < scripts/seed-rappi-agent.py
"""

from __future__ import annotations

import json
import sys

from src.agent import db


def main() -> int:
    from src.agent.seed import seed_rappi_buyer

    result = seed_rappi_buyer(db.init())
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
