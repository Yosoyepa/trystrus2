"""Tests for unified kernel HTTP routers (Decision, Audit, Evidence, Agent Bridge).

Verifies in-memory/in-process execution for all endpoints without external dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from src.api import deps
from src.api.audit.repository_memory import InMemoryLedgerRepository
from src.api.audit.service import LedgerService
from src.api.audit.signer_local import LocalEd25519Signer
from src.api.audit.witness_memory import InMemoryWitness
from src.api.decision.repository_memory import (
    InMemoryEscalationStore,
    InMemoryIdempotencyStore,
    InMemoryMandateReader,
    InMemoryOfferCatalog,
    InMemoryOutboxWriter,
    InMemoryPurchaseStore,
    InMemoryReservationStore,
    InMemoryVelocityStore,
)
from src.api.decision.service import DecisionService
from src.api.domain.models import (
    MandateClaims,
    MandateLimits,
    MandateScope,
    MandateStatus,
    MandateValidity,
    MaxTxnLimit,
    Offer,
)
from src.api.evidence import ChainVerdict, EvidenceService
from src.api.main import create_app

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


# ── SQLite Fake Connection for Agent Bridge Tests ───────────────────────────
class FakeAgentConn:
    """In-memory SQLite wrapper mimicking psycopg Conn for agent subsystems."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT,
                role TEXT NOT NULL DEFAULT 'member',
                token_hash TEXT UNIQUE, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                owner_id TEXT, approver_id TEXT, auditor_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                public_jwk TEXT NOT NULL,
                current_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_versions (
                agent_id TEXT NOT NULL, version INTEGER NOT NULL,
                ontology TEXT NOT NULL, model_cfg TEXT NOT NULL,
                changed_by TEXT, reason TEXT, created_at TEXT NOT NULL,
                PRIMARY KEY (agent_id, version)
            );
            CREATE TABLE IF NOT EXISTS mandates (
                jti TEXT PRIMARY KEY, user_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                claims TEXT NOT NULL, token TEXT NOT NULL,
                reserved_amount TEXT NOT NULL DEFAULT '0.00',
                spent_total TEXT NOT NULL DEFAULT '0.00',
                txn_count INTEGER NOT NULL DEFAULT 0,
                parent_jti TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offers (
                id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, category TEXT NOT NULL,
                title TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
                origin TEXT, destination TEXT, depart_date TEXT,
                description TEXT, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS watches (
                id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, mandate_jti TEXT NOT NULL,
                created_by TEXT, query TEXT NOT NULL, threshold TEXT NOT NULL,
                interval_s INTEGER NOT NULL DEFAULT 300,
                autobuy INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                last_checked_at TEXT, last_seen_price TEXT, fired_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rate_buckets (
                key TEXT PRIMARY KEY, tokens REAL NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS counters (
                key TEXT NOT NULL, window_key TEXT NOT NULL, value REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL, PRIMARY KEY (key, window_key)
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                role TEXT NOT NULL, text TEXT NOT NULL, run_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                run_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
                agent_version INTEGER NOT NULL,
                mandate_jti TEXT NOT NULL, session_id TEXT,
                node TEXT NOT NULL, state TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                escalation_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chains (
                chain_key TEXT PRIMARY KEY, head_hash TEXT NOT NULL,
                length INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, chain_key TEXT,
                chain_seq INTEGER, event_id TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL, actor TEXT, agent_id TEXT,
                run_id TEXT, mandate_jti TEXT, payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
                root_sig TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL, aggregate_id TEXT NOT NULL, payload TEXT NOT NULL,
                relayed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                created_at TEXT NOT NULL
            );
            """
        )

    def execute(self, sql: str, args: tuple = ()):
        clean_sql = sql.replace("FOR UPDATE", "")
        if "pg_locks" in sql:
            class DummyCursor:
                def fetchall(self):
                    return []

                def fetchone(self):
                    return None

            return DummyCursor()
        stripped = clean_sql.strip().upper()
        if stripped in ("BEGIN", "BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"):
            return self
        return self._conn.execute(clean_sql, args)

    def fetchall(self) -> list[Any]:
        return []

    def fetchone(self) -> Any | None:
        return None

    def close(self) -> None:
        self._conn.close()


# ── Fixtures and Test Helpers ────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def clean_singletons():
    deps.reset()
    yield
    deps.reset()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def decision_harness():
    now = datetime.now(UTC)
    mandate = MandateClaims(
        jti="mandate-test-1",
        agent="agent-test",
        currency="USD",
        scope=MandateScope(categories=("flights",), merchants=("vuelaya",)),
        limits=MandateLimits(
            max_per_txn="150.00",
            total_budget="500.00",
            max_txn=MaxTxnLimit(count=10, period="day"),
        ),
        validity=MandateValidity(
            not_before=now - timedelta(days=1),
            expires_at=now + timedelta(days=1),
        ),
        status=MandateStatus.ACTIVE,
    )
    offer_normal = Offer(
        offer_id="offer-100",
        merchant_id="vuelaya",
        category="flights",
        amount="100.00",
        currency="USD",
    )
    offer_expensive = Offer(
        offer_id="offer-200",
        merchant_id="vuelaya",
        category="flights",
        amount="200.00",
        currency="USD",
    )

    reader = InMemoryMandateReader()
    reader.put(mandate)
    catalog = InMemoryOfferCatalog()
    catalog.put(offer_normal)
    catalog.put(offer_expensive)
    velocity = InMemoryVelocityStore()
    reservation = InMemoryReservationStore()
    reservation.register_mandate(mandate.jti, total_budget="500.00")
    purchases = InMemoryPurchaseStore()
    escalations = InMemoryEscalationStore()
    outbox = InMemoryOutboxWriter()
    secret = "test-secret"
    idempotency = InMemoryIdempotencyStore(secret)

    service = DecisionService(
        mandate_reader=reader,
        offer_catalog=catalog,
        velocity_store=velocity,
        reservation_store=reservation,
        purchase_store=purchases,
        escalation_store=escalations,
        outbox=outbox,
        idempotency_store=idempotency,
        idempotency_secret=secret,
    )
    deps.set_decision_service(service)
    return {
        "service": service,
        "mandate": mandate,
        "offer_normal": offer_normal,
        "offer_expensive": offer_expensive,
    }


# ── Decision Router Tests ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_verify_mandate_approved(app, decision_harness):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/mandates/mandate-test-1/verify",
            json={
                "offer_id": "offer-100",
                "amount": "100.00",
                "currency": "USD",
                "agent": "agent-test",
                "merchant_id": "vuelaya",
                "category": "flights",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "APPROVED"
        assert data["status"] == "pending_capture"
        assert data["reservation_id"] is not None


@pytest.mark.asyncio
async def test_verify_mandate_escalated(app, decision_harness):
    # Trigger burst velocity to cause escalation (default max_intents is 3)
    velocity = decision_harness["service"].velocity_store
    now = datetime.now(UTC)
    for _ in range(4):
        velocity.increment_intent("mandate-test-1", "1.00", now)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/mandates/mandate-test-1/verify",
            json={
                "jti": "intent-esc-1",
                "offer_id": "offer-100",
                "amount": "100.00",
                "currency": "USD",
                "agent": "agent-test",
                "merchant_id": "vuelaya",
                "category": "flights",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "ESCALATED"
        assert data["status"] == "awaiting_escalation"
        assert data["escalation_id"] is not None


@pytest.mark.asyncio
async def test_verify_mandate_rejected_budget(app, decision_harness):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register an offer exceeding max_per_txn (200 > 150)
        resp = await client.post(
            "/mandates/mandate-test-1/verify",
            json={
                "jti": "intent-exceed-per-txn",
                "offer_id": "offer-200",
                "amount": "200.00",
                "currency": "USD",
                "agent": "agent-test",
                "merchant_id": "vuelaya",
                "category": "flights",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "REJECTED"
        assert data["reason_code"] == "AMOUNT_EXCEEDS_PER_TXN"

        # Register a tight-budget mandate (total_budget=100.00, max_per_txn=150.00)
        now = datetime.now(UTC)
        tight_mandate = MandateClaims(
            jti="mandate-tight-budget",
            agent="agent-test",
            currency="USD",
            scope=MandateScope(categories=("flights",), merchants=("vuelaya",)),
            limits=MandateLimits(
                max_per_txn="150.00",
                total_budget="100.00",
                max_txn=MaxTxnLimit(count=10, period="day"),
            ),
            validity=MandateValidity(
                not_before=now - timedelta(days=1),
                expires_at=now + timedelta(days=1),
            ),
            status=MandateStatus.ACTIVE,
        )
        decision_harness["service"].mandate_reader.put(tight_mandate)
        catalog = decision_harness["service"].offer_catalog
        catalog.put(
            Offer(
                offer_id="offer-120",
                merchant_id="vuelaya",
                category="flights",
                amount="120.00",
                currency="USD",
            )
        )

        resp2 = await client.post(
            "/mandates/mandate-tight-budget/verify",
            json={
                "jti": "intent-budget-exceeded",
                "offer_id": "offer-120",
                "amount": "120.00",
                "currency": "USD",
                "agent": "agent-test",
                "merchant_id": "vuelaya",
                "category": "flights",
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["decision"] == "REJECTED"
        assert data2["reason_code"] == "BUDGET_EXCEEDED"


@pytest.mark.asyncio
async def test_decision_idempotency(app, decision_harness):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "jti": "intent-idem-1",
            "mandate_jti": "mandate-test-1",
            "offer_id": "offer-100",
            "amount": "100.00",
            "currency": "USD",
            "agent": "agent-test",
            "merchant_id": "vuelaya",
            "category": "flights",
            "idempotency_key": "idem-key-1",
        }
        resp1 = await client.post("/mandates/mandate-test-1/verify", json=payload)
        resp2 = await client.post("/mandates/mandate-test-1/verify", json=payload)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["reservation_id"] == resp2.json()["reservation_id"]


@pytest.mark.asyncio
async def test_purchases_endpoints(app, decision_harness):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        verify_resp = await client.post(
            "/purchases/verify",
            json={
                "jti": "intent-p1",
                "mandate_jti": "mandate-test-1",
                "offer_id": "offer-100",
                "amount": "100.00",
                "currency": "USD",
                "agent": "agent-test",
                "merchant_id": "vuelaya",
                "category": "flights",
            },
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["decision"] == "APPROVED"

        # Submit purchase
        sub_resp = await client.post(
            "/purchases",
            json={
                "jti": "intent-p2",
                "purchase_id": "pur-test-123",
                "mandate_jti": "mandate-test-1",
                "offer_id": "offer-100",
                "amount": "100.00",
                "currency": "USD",
                "agent": "agent-test",
                "merchant_id": "vuelaya",
                "category": "flights",
            },
        )
        assert sub_resp.status_code == 202
        assert sub_resp.json()["purchase_id"] == "pur-test-123"

        # Get purchase
        get_resp = await client.get("/purchases/pur-test-123")
        assert get_resp.status_code == 200
        assert get_resp.json()["purchase_id"] == "pur-test-123"
        assert get_resp.json()["status"] == "pending_capture"


# ── Audit Router Tests ───────────────────────────────────────────────────────
@pytest.fixture
def audit_service():
    repo = InMemoryLedgerRepository()
    signer = LocalEd25519Signer()
    witness = InMemoryWitness()
    service = LedgerService(repo, signer, witness)

    # Append sample events
    service.append("mandate.created", "mandate-a", {"init": True})
    service.append("purchase.verified", "mandate-a", {"amount": "50.00"})
    service.append("purchase.captured", "mandate-b", {"amount": "30.00"})

    # Sign root checkpoint over 1..3
    service.sign_root(1, 3)

    deps.set_ledger_service(service)
    return service


@pytest.mark.asyncio
async def test_audit_events_list_and_filter(app, audit_service):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # All events
        resp = await client.get("/audit/events")
        assert resp.status_code == 200
        events = resp.json()
        assert len(events) == 3

        # Filter by mandate
        resp_a = await client.get("/audit/events?mandate_id=mandate-a")
        assert resp_a.status_code == 200
        assert len(resp_a.json()) == 2

        # Pagination with after_seq and limit
        resp_p = await client.get("/audit/events?after_seq=1&limit=1")
        assert resp_p.status_code == 200
        page = resp_p.json()
        assert len(page) == 1
        assert page[0]["seq"] == 2


@pytest.mark.asyncio
async def test_audit_verify_valid_and_tamper(app, audit_service):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Check initial validity
        v_resp = await client.post("/audit/verify")
        assert v_resp.status_code == 200
        assert v_resp.json()["valid"] is True
        assert v_resp.json()["events_checked"] == 3

        # GET version
        v_get = await client.get("/audit/verify")
        assert v_get.status_code == 200
        assert v_get.json()["valid"] is True

        # Tamper event at seq 2
        t_resp = await client.post(
            "/audit/tamper",
            json={"seq": 2, "field_name": "payload", "value": {"corrupted": True}},
        )
        assert t_resp.status_code == 200
        assert t_resp.json()["status"] == "tampered"

        # Verify should now fail and identify seq 2
        v_fail = await client.post("/audit/verify")
        assert v_fail.status_code == 200
        data = v_fail.json()
        assert data["valid"] is False
        assert data["first_bad_seq"] == 2


# ── Evidence Router Tests ────────────────────────────────────────────────────
class FakeEvidenceReaders:
    def __init__(self) -> None:
        self.purchase = {
            "purchase_id": "pur-ev-1",
            "intent_jti": "jti-ev-1",
            "status": "captured",
            "reason_code": None,
            "reservation_id": "res-1",
        }
        self.mandate = {"jti": "jti-ev-1", "agent": "agt_flights"}
        self.intent = {"jti": "jti-ev-1", "amount": "130.00"}
        self.receipt = {"capture_id": "cap-1", "amount": "130.00"}
        self.events = ({"seq": 1, "type": "purchase.verified", "hash": "h" * 64},)
        self.checkpoint = {"seq_start": 1, "seq_end": 1, "root_sig": "s" * 128}

    def get_purchase(self, purchase_id: str):
        return dict(self.purchase) if purchase_id == "pur-ev-1" else None

    def get_claims(self, mandate_jti: str):
        return dict(self.mandate) if mandate_jti == "jti-ev-1" else None

    def get_intent(self, intent_jti: str):
        return dict(self.intent) if intent_jti == "jti-ev-1" else None

    def get_receipt(self, purchase_id: str):
        return dict(self.receipt)

    def events_for(self, mandate_jti: str):
        return self.events

    def chain_verdict(self, mandate_jti: str):
        return ChainVerdict(ok=True)

    def latest_checkpoint(self, mandate_jti: str):
        return dict(self.checkpoint)


@pytest.mark.asyncio
async def test_evidence_pack_router(app):
    readers = FakeEvidenceReaders()
    service = EvidenceService(
        purchases=readers,
        mandates=readers,
        intents=readers,
        receipts=readers,
        ledger=readers,
        witness=readers,
    )
    deps.set_evidence_service(service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Success
        resp = await client.get("/purchases/pur-ev-1/evidence-pack")
        assert resp.status_code == 200
        pack = resp.json()
        assert pack["purchase_id"] == "pur-ev-1"
        assert pack["integrity"] == "ok"
        assert pack["digest"] is not None

        # 404 on unknown purchase
        resp_404 = await client.get("/purchases/pur-unknown/evidence-pack")
        assert resp_404.status_code == 404


# ── Agent Bridge Router Tests ────────────────────────────────────────────────
@pytest.fixture
def agent_conn():
    conn = FakeAgentConn()
    now_iso = datetime.now(UTC).isoformat()
    # Seed required rows for agent chat & watches
    conn.execute(
        "INSERT INTO agents(id, name, public_jwk, current_version, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        ("agt_flights", "Flight Agent", "{}", 1, now_iso, now_iso),
    )
    conn.execute(
        "INSERT INTO agent_versions(agent_id, version, ontology, model_cfg, created_at) "
        "VALUES(?, ?, ?, ?, ?)",
        ("agt_flights", 1, "{}", "{}", now_iso),
    )
    claims_json = json.dumps(
        {
            "jti": "mandate-agent-1",
            "agent": "agt_flights",
            "user_id": "usr_marta",
            "limits": {"max_per_txn": "150.00"},
            "scope": {"categories": ["flights"]},
        }
    )
    conn.execute(
        "INSERT INTO mandates(jti, user_id, agent_id, status, claims, token, "
        "created_at, updated_at) VALUES(?, ?, ?, 'active', ?, 'tok-1', ?, ?)",
        ("mandate-agent-1", "usr_marta", "agt_flights", claims_json, now_iso, now_iso),
    )

    deps.set_agent_conn(conn)
    return conn


@pytest.mark.asyncio
async def test_agent_bridge_limits(app, agent_conn):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agent/limits")
        assert resp.status_code == 200
        data = resp.json()
        assert "quota" in data
        assert "buckets" in data
        assert "counters" in data
        assert "locks" in data


@pytest.mark.asyncio
async def test_agent_bridge_watches(app, agent_conn):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create watch
        resp = await client.post(
            "/agent/watches",
            json={
                "agent_id": "agt_flights",
                "mandate_jti": "mandate-agent-1",
                "query": {"origin": "BOG", "destination": "COR"},
                "max_price": 120.0,
                "interval_s": 300,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "watch_id" in data

        # List watches
        list_resp = await client.get("/agent/watches")
        assert list_resp.status_code == 200
        watches = list_resp.json()
        assert len(watches) == 1
        assert watches[0]["agent_id"] == "agt_flights"


@pytest.mark.asyncio
async def test_agent_bridge_runs_and_transcript(app, agent_conn):
    now_iso = datetime.now(UTC).isoformat()
    agent_conn.execute(
        "INSERT INTO agent_runs(run_id, agent_id, agent_version, mandate_jti, session_id, "
        "node, state, status, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "run-100",
            "agt_flights",
            1,
            "mandate-agent-1",
            "sess-100",
            "search",
            json.dumps({"offers": []}),
            "running",
            now_iso,
            now_iso,
        ),
    )
    agent_conn.execute(
        "INSERT INTO chat_messages(session_id, role, text, run_id, created_at) "
        "VALUES(?, ?, ?, ?, ?)",
        ("sess-100", "user", "Find flights to COR", "run-100", now_iso),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # List runs
        runs_resp = await client.get("/agent/runs")
        assert runs_resp.status_code == 200
        runs = runs_resp.json()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "run-100"

        # Transcript
        tr_resp = await client.get("/agent/transcript?session_id=sess-100")
        assert tr_resp.status_code == 200
        transcript = tr_resp.json()
        assert len(transcript) == 1
        assert transcript[0]["text"] == "Find flights to COR"


@pytest.mark.asyncio
async def test_agent_bridge_ask(app, agent_conn):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Turn with an approval keyword
        resp = await client.post(
            "/agent/ask",
            json={
                "text": "approve",
                "agent_id": "agt_flights",
                "mandate_jti": "mandate-agent-1",
                "session_id": "sess-new",
                "person": "buyer",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-new"
        assert "replies" in data
