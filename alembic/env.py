"""Alembic environment.

The schema lives in one place — `src.agent.db.SCHEMA` — and the migration
applies it. Keeping a second copy of the DDL here is how dev and prod drift
apart, which is the exact failure this migration exists to prevent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get(
        "DATABASE_URL", "postgresql://trytrust:trytrust@localhost:5432/trytrust"
    ).replace("postgresql://", "postgresql+psycopg://"),
)

target_metadata = None  # raw DDL, no ORM models


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
