"""Console credentials

The console recorded who *claimed* to make a change. An audit trail of
unverified claims is weaker than it looks, so mutations now need a token and
the trail records an authenticated principal.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS token_hash TEXT")
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE people ADD CONSTRAINT people_token_hash_key UNIQUE (token_hash);
        EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS token_hash")
