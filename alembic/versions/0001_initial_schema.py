"""Initial TryTrust schema

Applies `src.agent.db.SCHEMA` verbatim so there is exactly one description of
the database in the repository. Every statement in it is `IF NOT EXISTS` or
`CREATE OR REPLACE`, so re-running the migration is safe.

Revision ID: 0001
Revises:
"""
from alembic import op

from src.agent.db import SCHEMA, TABLES

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(SCHEMA)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS " + ", ".join(TABLES) + " CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trytrust_append_only() CASCADE")
