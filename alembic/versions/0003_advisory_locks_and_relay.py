"""Retire the lock table; index the outbox for the relay

Single-flight moved to Postgres advisory locks. They need no TTL because the
lock dies with the session: a crashed holder releases immediately, where the
table needed a timeout long enough to be safe and short enough to be useful.

Also indexes the outbox claim path. The relay drains with
`WHERE relayed_at IS NULL AND attempts < n ORDER BY seq FOR UPDATE SKIP LOCKED`,
and without an index that is a sequential scan on every pass of every worker.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS locks")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbox_claim "
               "ON outbox(relayed_at, attempts, seq)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_watches_claim "
               "ON watches(status, last_checked_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_watches_claim")
    op.execute("DROP INDEX IF EXISTS ix_outbox_claim")
    op.execute("""
        CREATE TABLE IF NOT EXISTS locks (
          name TEXT PRIMARY KEY, holder TEXT NOT NULL,
          acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL)
    """)
