import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from src.api.audit.chain import validate_chain
from src.api.audit.hashing import GENESIS_PREV_HASH
from src.api.audit.models import AuditEvent
from src.api.audit.repository_memory import InMemoryLedgerRepository
from src.api.audit.repository_postgres import PostgresLedgerRepository


class TestInMemoryRepository:
    def test_genesis_prev_hash_and_chaining(self) -> None:
        repo = InMemoryLedgerRepository()
        e1 = repo.append(
            mandate_id="mdt_01",
            type="mandate.created",
            payload={"limits": {"max_per_txn": 150}},
        )
        assert e1.seq == 1
        assert e1.prev_hash == GENESIS_PREV_HASH
        assert e1.root_sig is None

        e2 = repo.append(
            mandate_id="mdt_01",
            type="purchase.verified",
            payload={"amount": "130.00"},
        )
        assert e2.seq == 2
        assert e2.prev_hash == e1.hash

        events = repo.get_all()
        assert len(events) == 2
        assert validate_chain(events).ok is True

    def test_range_and_mandate_queries(self) -> None:
        repo = InMemoryLedgerRepository()
        repo.append(mandate_id="mdt_01", type="mandate.created", payload={"id": 1})
        repo.append(mandate_id="mdt_02", type="mandate.created", payload={"id": 2})
        repo.append(mandate_id="mdt_01", type="purchase.verified", payload={"id": 3})

        m1_events = repo.get_by_mandate("mdt_01")
        assert len(m1_events) == 2
        assert [e.seq for e in m1_events] == [1, 3]

        range_events = repo.get_range(2, 3)
        assert len(range_events) == 2
        assert [e.seq for e in range_events] == [2, 3]

        tail = repo.get_tail()
        assert tail is not None
        assert tail.seq == 3

    def test_annotate_root_guard(self) -> None:
        repo = InMemoryLedgerRepository()
        repo.append(mandate_id="mdt_01", type="mandate.created", payload={})
        repo.append(mandate_id="mdt_01", type="purchase.verified", payload={})
        repo.append(mandate_id="mdt_01", type="purchase.captured", payload={})

        # Annotate range 1-2
        updated = repo.annotate_root(1, 2, "sig_root_1_2")
        assert updated == 2

        events = repo.get_all()
        assert events[0].root_sig == "sig_root_1_2"
        assert events[1].root_sig == "sig_root_1_2"
        assert events[2].root_sig is None

        # Chain remains completely valid
        assert validate_chain(events).ok is True

        # Second annotation attempt on same range updates 0
        updated_again = repo.annotate_root(1, 2, "sig_root_override")
        assert updated_again == 0
        assert repo.get_all()[0].root_sig == "sig_root_1_2"

    def test_tamper_hook(self) -> None:
        repo = InMemoryLedgerRepository()
        repo.append(mandate_id="mdt_01", type="mandate.created", payload={"amount": "100.00"})
        repo.append(mandate_id="mdt_01", type="purchase.captured", payload={"amount": "100.00"})

        assert validate_chain(repo.get_all()).ok is True

        # Tamper payload of event 1
        repo.tamper(1, "payload", {"amount": "999.00"})
        result = validate_chain(repo.get_all())
        assert result.ok is False
        assert result.first_bad_seq == 1

    def test_concurrent_appends_do_not_fork(self) -> None:
        repo = InMemoryLedgerRepository()
        num_threads = 8
        events_per_thread = 25

        def worker(thread_id: int) -> list[AuditEvent]:
            created = []
            for i in range(events_per_thread):
                ev = repo.append(
                    mandate_id=f"mdt_t{thread_id}",
                    type="event.appended",
                    payload={"thread": thread_id, "i": i},
                )
                created.append(ev)
            return created

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(worker, t) for t in range(num_threads)]
            for f in futures:
                f.result()

        all_events = repo.get_all()
        assert len(all_events) == num_threads * events_per_thread
        # Sequence numbers must be 1..N strictly contiguous
        seqs = [e.seq for e in all_events]
        assert seqs == list(range(1, len(all_events) + 1))
        # Hash chain must be 100% unbroken
        result = validate_chain(all_events)
        assert result.ok is True
        assert result.verified_count == len(all_events)


@pytest.mark.db
class TestPostgresRepository:
    @pytest.fixture(autouse=True)
    def setup_postgres(self) -> None:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not set; skipping Postgres integration tests")

        try:
            import psycopg

            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS audit_events (
                          seq BIGSERIAL PRIMARY KEY,
                          mandate_id TEXT NOT NULL,
                          type TEXT NOT NULL,
                          payload JSONB NOT NULL,
                          prev_hash CHAR(64) NOT NULL,
                          hash CHAR(64) NOT NULL,
                          root_sig TEXT,
                          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        );
                        TRUNCATE TABLE audit_events RESTART IDENTITY;
                        """
                    )
                    conn.commit()
        except Exception as exc:
            pytest.skip(f"PostgreSQL unreachable: {exc}")

    def test_postgres_append_and_chain(self) -> None:
        repo = PostgresLedgerRepository()
        e1 = repo.append(mandate_id="mdt_pg1", type="mandate.created", payload={"test": True})
        e2 = repo.append(mandate_id="mdt_pg1", type="purchase.verified", payload={"step": 2})

        assert e1.seq == 1
        assert e1.prev_hash == GENESIS_PREV_HASH
        assert e2.seq == 2
        assert e2.prev_hash == e1.hash

        events = repo.get_all()
        assert len(events) == 2
        assert validate_chain(events).ok is True

    def test_postgres_concurrent_appends_serialize_cleanly(self) -> None:
        repo = PostgresLedgerRepository()
        num_threads = 4
        events_per_thread = 10

        def worker(t_id: int) -> None:
            for i in range(events_per_thread):
                repo.append(
                    mandate_id=f"mdt_pg_t{t_id}",
                    type="purchase.requested",
                    payload={"thread": t_id, "iter": i},
                )

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(worker, t) for t in range(num_threads)]
            for f in futures:
                f.result()

        events = repo.get_all()
        assert len(events) == num_threads * events_per_thread
        assert validate_chain(events).ok is True
