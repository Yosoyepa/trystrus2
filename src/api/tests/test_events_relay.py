"""Tests for outbox relay and signed webhooks (in-memory and PostgreSQL)."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest
import respx

from src.api.audit.signer_local import LocalEd25519Signer
from src.api.events.ports import OutboxEvent
from src.api.events.relay import OutboxRelay, PostgresOutboxStore
from src.api.events.sinks_memory import InMemoryOutboxStore, InMemorySink
from src.api.events.webhook_signed import SignedWebhookPoster


class TestOutboxRelayUnit:
    def test_delivery_in_order(self) -> None:
        store = InMemoryOutboxStore()
        sink = InMemorySink("audit_bot")
        relay = OutboxRelay(store, default_sinks=[sink])

        store.append(
            OutboxEvent(
                event_id="evt_01",
                type="mandate.created",
                aggregate_id="mdt_01",
                payload={"step": 1},
                created_at=datetime.now(UTC),
            )
        )
        store.append(
            OutboxEvent(
                event_id="evt_02",
                type="purchase.verified",
                aggregate_id="pur_01",
                payload={"step": 2},
                created_at=datetime.now(UTC),
            )
        )

        drained = relay.drain()
        assert drained == 2
        assert len(sink.received_events) == 2
        assert [e.event_id for e in sink.received_events] == ["evt_01", "evt_02"]

        # Second drain finds nothing unrelayed
        assert relay.drain() == 0

    def test_transient_failure_redelivers_with_isolation(self) -> None:
        store = InMemoryOutboxStore()
        sink = InMemorySink("flaky_sink")
        relay = OutboxRelay(store, default_sinks=[sink])

        store.append(
            OutboxEvent(
                event_id="evt_ok1",
                type="t1",
                aggregate_id="a1",
                payload={},
                created_at=datetime.now(UTC),
            )
        )
        store.append(
            OutboxEvent(
                event_id="evt_flaky",
                type="t2",
                aggregate_id="a2",
                payload={},
                created_at=datetime.now(UTC),
            )
        )
        store.append(
            OutboxEvent(
                event_id="evt_ok2",
                type="t3",
                aggregate_id="a3",
                payload={},
                created_at=datetime.now(UTC),
            )
        )

        # Make e2 fail once
        sink.fail_times_for_event("evt_flaky", times=1)

        # First drain: e1 and e3 succeed, e2 fails
        drained_first = relay.drain()
        assert drained_first == 2

        # Check e2 is still unrelayed
        unrelayed = [e for e in store.get_all() if e.relayed_at is None]
        assert len(unrelayed) == 1
        assert unrelayed[0].event_id == "evt_flaky"

        # Second drain: e2 succeeds now that flaky count is exhausted
        drained_second = relay.drain()
        assert drained_second == 1
        assert len(sink.received_events) == 3

    def test_event_without_sinks_marks_relayed(self) -> None:
        store = InMemoryOutboxStore()
        # No default sinks, and route for "unhandled.event" is empty
        relay = OutboxRelay(store, routes={}, default_sinks=[])

        store.append(
            OutboxEvent(
                event_id="evt_no_sink",
                type="unhandled.event",
                aggregate_id="a1",
                payload={"info": "logged"},
                created_at=datetime.now(UTC),
            )
        )

        drained = relay.drain()
        assert drained == 1
        all_events = store.get_all()
        assert all_events[0].relayed_at is not None

    def test_signed_webhook_poster_dispatch(self) -> None:
        signer = LocalEd25519Signer.generate()
        target_url = "https://merchant.vuelaya.example/webhook"
        event = OutboxEvent(
            event_id="evt_wh_01",
            type="purchase.captured",
            aggregate_id="pur_100",
            payload={"amount": "130.00", "currency": "USD"},
            created_at=datetime(2026, 8, 29, 15, 0, 0, tzinfo=UTC),
        )

        with respx.mock:
            route = respx.post(target_url).respond(status_code=200, json={"received": True})

            poster = SignedWebhookPoster(target_url, signer)
            poster.handle(event)

            assert route.called
            req = route.calls.last.request

            # Verify headers
            assert req.headers["Content-Type"] == "application/json"
            assert "X-Aval-Signature" in req.headers
            sig_header = req.headers["X-Aval-Signature"]
            assert sig_header.startswith("ed25519=")
            sig_hex = sig_header.split("=")[1]
            sig_bytes = bytes.fromhex(sig_hex)

            # Verify signature against request content
            assert signer.verify(req.content, sig_bytes) is True

            # Verify body JSON content
            body_dict = json.loads(req.content.decode("utf-8"))
            assert body_dict["event_id"] == "evt_wh_01"
            assert body_dict["type"] == "purchase.captured"

    def test_signed_webhook_poster_http_failure_raises(self) -> None:
        signer = LocalEd25519Signer.generate()
        target_url = "https://merchant.vuelaya.example/webhook_fail"
        event = OutboxEvent(
            event_id="evt_wh_fail",
            type="purchase.captured",
            aggregate_id="pur_101",
            payload={},
            created_at=datetime.now(UTC),
        )

        with respx.mock:
            respx.post(target_url).respond(status_code=500)
            poster = SignedWebhookPoster(target_url, signer)
            with pytest.raises(httpx.HTTPStatusError):
                poster.handle(event)


@pytest.mark.db
class TestPostgresOutboxRelay:
    @pytest.fixture(autouse=True)
    def setup_outbox(self) -> None:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not set; skipping Postgres outbox tests")

        try:
            import psycopg

            with psycopg.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS outbox (
                          seq BIGSERIAL PRIMARY KEY,
                          event_id TEXT UNIQUE NOT NULL,
                          type TEXT NOT NULL,
                          aggregate_id TEXT NOT NULL,
                          payload JSONB NOT NULL,
                          relayed_at TIMESTAMPTZ,
                          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        );
                        TRUNCATE TABLE outbox RESTART IDENTITY;
                        """
                    )
                    conn.commit()
        except Exception as exc:
            pytest.skip(f"PostgreSQL unreachable: {exc}")

    def test_concurrent_relays_do_not_duplicate_skip_locked(self) -> None:
        store = PostgresOutboxStore()
        total_events = 20
        for i in range(total_events):
            store.append(
                OutboxEvent(
                    event_id=f"evt_pg_{i}",
                    type="mandate.created",
                    aggregate_id=f"mdt_{i}",
                    payload={"index": i},
                    created_at=datetime.now(UTC),
                )
            )

        sink = InMemorySink("concurrent_shared_sink")
        num_workers = 4

        def worker(_worker_id: int) -> int:
            relay = OutboxRelay(store, default_sinks=[sink])
            return relay.drain(limit=10)

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, w) for w in range(num_workers)]
            drained_counts = [f.result() for f in futures]

        # Total drained count across all workers must equal total_events
        assert sum(drained_counts) == total_events
        # No duplicates received at sink
        received_ids = [e.event_id for e in sink.received_events]
        assert len(received_ids) == total_events
        assert len(set(received_ids)) == total_events
