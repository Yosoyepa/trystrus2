"""Convert offers.amount to NUMERIC(12,2)

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN amount TYPE NUMERIC(12,2) USING (amount::numeric(12,2));
        EXCEPTION WHEN others THEN NULL; END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN amount TYPE TEXT USING (amount::text);
        EXCEPTION WHEN others THEN NULL; END $$
    """)
