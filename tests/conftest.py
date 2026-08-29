"""Shared fixtures.

Integration tests run against a real Postgres, not an in-memory substitute:
the guarded UPDATE that enforces the state machine *is* the implementation, and
SQLite cannot express it. If no database is reachable they skip rather than
pass, so a green suite never means "we skipped the part that matters".
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DEFAULT_DB = f"postgresql+asyncpg:///{os.environ.get('AVAL_DB_NAME', 'aval')}"
DATABASE_URL = os.environ.get("AVAL_TEST_DATABASE_URL", DEFAULT_DB)

# Every table this suite touches, in dependency order for truncation.
TABLES = (
    "webauthn_challenges", "webauthn_credentials",
    "payment_instruments", "escalations", "outbox", "mandates",
    "yuno_idempotency", "yuno_disputes", "yuno_payments",
    "yuno_payment_tokens", "yuno_setup_tokens",
)


async def _database_reachable(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def database_url() -> str:
    if not await _database_reachable(DATABASE_URL):
        pytest.skip(
            f"no Postgres at {DATABASE_URL} — run scripts/db-bootstrap.sh",
            allow_module_level=True,
        )
    return DATABASE_URL


@pytest_asyncio.fixture
async def engine(database_url):
    engine = create_async_engine(database_url, poolclass=None)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """A session on a clean set of tables.

    Truncation rather than a transaction rollback: the code under test commits
    (revocation must be visible to a concurrent reader the instant it lands),
    so wrapping it in an outer transaction would test something else.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session


@pytest.fixture
def user_id() -> str:
    return f"usr_{uuid.uuid4().hex[:8]}"
