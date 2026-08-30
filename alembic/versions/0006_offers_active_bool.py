"""Convert offers.active to boolean

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN active DROP DEFAULT;
            ALTER TABLE offers ALTER COLUMN active TYPE BOOLEAN USING (active::text = '1' OR active::text = 'true');
            ALTER TABLE offers ALTER COLUMN active SET DEFAULT true;
        EXCEPTION WHEN others THEN NULL; END $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN active DROP DEFAULT;
            ALTER TABLE offers ALTER COLUMN active TYPE INTEGER USING (CASE WHEN active THEN 1 ELSE 0 END);
            ALTER TABLE offers ALTER COLUMN active SET DEFAULT 1;
        EXCEPTION WHEN others THEN NULL; END $$
    """)
