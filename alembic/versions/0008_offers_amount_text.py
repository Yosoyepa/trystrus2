"""Align offers with the schema source of truth (aval/contracts/fixtures/schema.sql)

Revision ID: 0008
Revises: 0007

0007 made `offers.amount` NUMERIC(12,2), but the unified schema (decision
0029) keeps money as TEXT so no comparison depends on how the server
normalises a numeric literal. The merchant model writes TEXT; a NUMERIC
column makes the startup seed fail. This reverts that conversion and drops
`travel_date`, a column 0005 added that the unified schema never had.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN amount TYPE TEXT USING (amount::text);
        EXCEPTION WHEN others THEN NULL; END $$
    """)
    op.execute("ALTER TABLE offers DROP COLUMN IF EXISTS travel_date")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN amount TYPE NUMERIC(12,2)
            USING (amount::numeric(12,2));
        EXCEPTION WHEN others THEN NULL; END $$
    """)
