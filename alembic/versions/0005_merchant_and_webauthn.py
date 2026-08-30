"""Merchant and WebAuthn schema updates

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS travel_date DATE")
    op.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS depart_date TEXT")
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE offers ALTER COLUMN active DROP DEFAULT;
            ALTER TABLE offers ALTER COLUMN active TYPE BOOLEAN
                USING (active::text = '1' OR active::text = 'true');
            ALTER TABLE offers ALTER COLUMN active SET DEFAULT true;
        EXCEPTION WHEN others THEN NULL; END $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_orders (
            id TEXT PRIMARY KEY,
            offer_id TEXT NOT NULL,
            amount NUMERIC(12, 2) NOT NULL,
            currency TEXT NOT NULL,
            checkout_jwt TEXT UNIQUE NOT NULL,
            checkout_hash TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'quoted',
            purchase_id TEXT UNIQUE,
            receipt JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS webauthn_credentials (
            credential_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            public_key BYTEA NOT NULL,
            sign_count BIGINT NOT NULL DEFAULT 0,
            transports TEXT[]
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS webauthn_credentials CASCADE")
    op.execute("DROP TABLE IF EXISTS merchant_orders CASCADE")
    op.execute("ALTER TABLE offers DROP COLUMN IF EXISTS travel_date")
    op.execute("ALTER TABLE offers DROP COLUMN IF EXISTS depart_date")
