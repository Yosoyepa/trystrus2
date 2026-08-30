"""Reset every table to the unified schema source of truth

Revision ID: 0009
Revises: 0008

The dev database was created from the pre-unification kernel schema
(`src/api/db/schema.sql`, deleted in the 0029 decision): `escalations`
lacks `resolved_at`/`run_id`, uses `mandate_id` instead of `mandate_jti`,
TIMESTAMPTZ/JSONB where the unified schema uses TEXT. Alembic cannot see
that drift — 0001 executes the schema verbatim, so a database stamped at
head may still carry the old shape.

This migration drops everything and re-applies the current schema, which
converges any drifted database. Destructive by design: it ran first on
dev, where every row is seed data regenerated at service startup.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _reset() -> None:
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)

    from src.agent.db import SCHEMA, TABLES

    op.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trytrust_append_only() CASCADE")
    op.execute(SCHEMA)


def upgrade() -> None:
    _reset()


def downgrade() -> None:
    _reset()
