"""Async engine and session for the kernel.

Pool sizes are small on purpose: Cloud SQL `db-f1-micro` allows few
connections, and the demo's load is trivial (ADR-016). Two services sharing
one instance with default pools would exhaust it before the judges arrived.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings().database_url,
            pool_size=5,
            max_overflow=2,
            pool_pre_ping=True,  # Cloud Run scales to zero; connections go stale
        )
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine(), expire_on_commit=False, autoflush=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One transaction per request.

    Commits on success, rolls back on any exception. That boundary is what
    makes the outbox atomic: the event and the business change share this
    transaction, so they commit together or not at all (decision #10).
    """
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
