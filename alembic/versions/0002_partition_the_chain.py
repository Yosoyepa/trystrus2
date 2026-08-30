"""Partition the audit chain by mandate

A single global chain serialised every event in the system: writing entry N
means reading entry N-1, so all writers queued on one row. This gives each
mandate its own chain and restores one global proof with signed checkpoints
over every chain head.

Existing rows are backfilled into the chain they belong to, in their original
order, and re-hashed so each chain replays from genesis. Any checkpoint signed
before this migration describes the old single chain and will no longer match
— which is correct: the evidence structure changed, and pretending otherwise
would be the one thing an audit log must never do.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
from src.agent.db import SCHEMA

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS chain_key TEXT")
    op.execute("ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS chain_seq BIGINT")
    op.execute(SCHEMA)  # creates chains + checkpoints; everything else is IF NOT EXISTS

    # E1 blocks this migration's own backfill, which is the trigger working as
    # designed. A schema migration is the one legitimate rewrite of the log, so
    # it lowers the guard explicitly, in a transaction, and puts it back --
    # rather than the log quietly being writable all along.
    op.execute("ALTER TABLE audit_events DISABLE TRIGGER USER")

    # Every pre-existing event belongs to a chain: its mandate, its agent, or system.
    op.execute("""
        UPDATE audit_events SET chain_key = COALESCE(
            mandate_jti, CASE WHEN agent_id IS NOT NULL
                              THEN 'agent:' || agent_id END, 'system')
        WHERE chain_key IS NULL
    """)
    op.execute("""
        WITH ordered AS (
            SELECT seq, ROW_NUMBER() OVER (PARTITION BY chain_key ORDER BY seq) AS n
            FROM audit_events
        )
        UPDATE audit_events a SET chain_seq = o.n FROM ordered o
        WHERE a.seq = o.seq AND a.chain_seq IS NULL
    """)
    op.execute("ALTER TABLE audit_events ALTER COLUMN chain_key SET NOT NULL")
    op.execute("ALTER TABLE audit_events ALTER COLUMN chain_seq SET NOT NULL")
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE audit_events ADD CONSTRAINT audit_events_chain_key_chain_seq_key
                UNIQUE (chain_key, chain_seq);
        EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; END $$
    """)
    # Re-hash each chain from genesis, then record its head.
    op.execute("""
        INSERT INTO chains(chain_key, head_hash, length, updated_at)
        SELECT chain_key, repeat('0', 64), 0, now()::text
        FROM (SELECT DISTINCT chain_key FROM audit_events) c
        ON CONFLICT (chain_key) DO NOTHING
    """)
    _rehash()
    op.execute("ALTER TABLE audit_events ENABLE TRIGGER USER")


def _rehash() -> None:
    """Recompute hashes in Python — the digest is defined in exactly one place.

    Runs on Alembic's own connection so it stays inside the migration's
    transaction, where the append-only trigger is disabled. Rows are read with
    an explicit dict cursor because SQLAlchemy's connection does not carry the
    row factory the rest of the codebase assumes.
    """
    import json

    from psycopg.rows import dict_row
    from src.agent import audit

    raw = op.get_bind().connection.dbapi_connection
    with raw.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT chain_key FROM chains ORDER BY chain_key")
        keys = [r["chain_key"] for r in cur.fetchall()]
        for key in keys:
            cur.execute("SELECT * FROM audit_events WHERE chain_key=%s ORDER BY chain_seq", (key,))
            events = cur.fetchall()
            prev = audit.GENESIS
            for ev in events:
                rebuilt = {
                    "event_id": ev["event_id"],
                    "type": ev["type"],
                    "actor": ev["actor"],
                    "agent_id": ev["agent_id"],
                    "run_id": ev["run_id"],
                    "mandate_jti": ev["mandate_jti"],
                    "payload": json.loads(ev["payload"]),
                    "created_at": ev["created_at"],
                    "chain_key": key,
                }
                digest = audit._digest(prev, rebuilt)
                cur.execute(
                    "UPDATE audit_events SET prev_hash=%s, hash=%s WHERE seq=%s",
                    (prev, digest, ev["seq"]),
                )
                prev = digest
            cur.execute(
                "UPDATE chains SET head_hash=%s, length=%s WHERE chain_key=%s",
                (prev, len(events), key),
            )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS checkpoints, chains CASCADE")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS chain_key")
    op.execute("ALTER TABLE audit_events DROP COLUMN IF EXISTS chain_seq")
