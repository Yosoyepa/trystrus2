"""Aval Rappi bridge — the guarded execution edge (decision 0030).

Runs on the credential machine ONLY. The Rappi session token never leaves
this host; screenshots and PII never leave this host. Every money-moving
path requires a kernel-minted capture token verified against the kernel
JWKS, and DRY_RUN is the default.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
