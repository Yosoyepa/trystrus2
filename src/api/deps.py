"""Shared singletons for the kernel's routers.

Plain module-level accessors rather than FastAPI `Depends` for the stateless
services: they hold no per-request state, and tests override them by assigning
to the module. The database session stays a real dependency — that one *is*
per-request, because it is a transaction.
"""

from __future__ import annotations

from .config import settings
from .services.mandate_registry import MandateRegistry
from .services.passkey import PasskeyService
from .services.rail_client import YunoSimRail

_registry: MandateRegistry | None = None
_passkey: PasskeyService | None = None
_rail: YunoSimRail | None = None


def mandate_registry() -> MandateRegistry:
    global _registry
    if _registry is None:
        _registry = MandateRegistry()
    return _registry


def passkey_service() -> PasskeyService:
    global _passkey
    if _passkey is None:
        _passkey = PasskeyService()
    return _passkey


def rail() -> YunoSimRail:
    global _rail
    if _rail is None:
        _rail = YunoSimRail(base_url=settings().yuno_sim_url)
    return _rail


def reset() -> None:
    """Drop the singletons. For tests that change configuration."""
    global _registry, _passkey, _rail
    _registry = _passkey = _rail = None
