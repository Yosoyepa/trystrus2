"""The outbox relay: the half of decision #10 that was missing.

Events have been written to `outbox` in the same transaction as the business
change since the beginning (E4), and until now nothing ever drained them. That
is why nothing outside this process could hear the system.

The drain is `FOR UPDATE SKIP LOCKED`, exactly the pattern decision #10 named:
several relay workers can run against one outbox and each row goes to exactly
one of them, with no coordination and no broker to keep alive at 3 a.m.

Delivery is at-least-once, on purpose. A subscriber that cannot tolerate a
repeat should key on `event_id`, which is stable and unique — the same
discipline the rest of the system uses for idempotency (M1).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from .crypto.canonical import canonical_json
from .ids import now_iso

MAX_ATTEMPTS = int(os.environ.get("TT_RELAY_MAX_ATTEMPTS", "5"))
WEBHOOK_URL = os.environ.get("TT_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("TT_WEBHOOK_SECRET", "trytrust-dev-secret")


class Event(dict):
    """One outbox row: event_id, type, aggregate_id, payload, created_at."""


class EventSubscriber(Protocol):
    name: str

    def on_event(self, event: Event) -> None:
        """Deliver. Raise to have the event retried; return to accept it."""


# ── subscribers ──────────────────────────────────────────────────────────────
class LogSubscriber:
    """Always registered. Proves the relay ran even when nothing else listens."""

    name = "log"

    def __init__(self, sink: Callable[[str], None] | None = None):
        self.sink = sink
        self.seen: list[str] = []

    def on_event(self, event: Event) -> None:
        self.seen.append(event["event_id"])
        if self.sink:
            self.sink(f"[{event['created_at']}] {event['type']} {event['aggregate_id']}")


class SseBuffer:
    """A bounded in-memory tail the API can stream from `GET /events/stream`.

    Bounded on purpose: an unbounded buffer is a memory leak with a nice name.
    """

    name = "sse"

    def __init__(self, maxlen: int = 500):
        self.events: deque[Event] = deque(maxlen=maxlen)

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def since(self, event_id: str | None = None) -> list[Event]:
        if event_id is None:
            return list(self.events)
        out, found = [], False
        for e in self.events:
            if found:
                out.append(e)
            found = found or e["event_id"] == event_id
        return out


class WebhookSubscriber:
    """POSTs to a URL with an HMAC signature the receiver can check.

    The signature is over the canonical body, so a receiver verifies the exact
    bytes it was sent -- the same reason the rest of the system canonicalises
    before signing (C3).
    """

    name = "webhook"

    def __init__(self, url: str | None = None, secret: str | None = None, timeout: float = 5.0):
        self.url = url or WEBHOOK_URL
        self.secret = (secret or WEBHOOK_SECRET).encode()
        self.timeout = timeout

    def sign(self, body: bytes) -> str:
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()

    def on_event(self, event: Event) -> None:
        if not self.url:
            return  # not configured is not a failure
        import httpx

        body = canonical_json(dict(event)).encode("utf-8")
        response = httpx.post(
            self.url,
            content=body,
            timeout=self.timeout,
            headers={
                "Content-Type": "application/json",
                "X-TryTrust-Event": event["event_id"],
                "X-TryTrust-Signature": f"sha256={self.sign(body)}",
            },
        )
        response.raise_for_status()


# ── registry ─────────────────────────────────────────────────────────────────
SUBSCRIBERS: dict[str, EventSubscriber] = {}


def register(subscriber: EventSubscriber) -> EventSubscriber:
    SUBSCRIBERS[subscriber.name] = subscriber
    return subscriber


def unregister(name: str) -> None:
    SUBSCRIBERS.pop(name, None)


def default_subscribers() -> None:
    register(LogSubscriber())
    register(SseBuffer())
    if WEBHOOK_URL:
        register(WebhookSubscriber())


# ── the drain ────────────────────────────────────────────────────────────────
def drain(conn, *, batch: int = 50) -> dict[str, Any]:
    """One pass. Safe to run from several workers at once.

    Each worker claims a disjoint slice via SKIP LOCKED, delivers it, and marks
    it relayed inside the same transaction that holds the claim -- so a worker
    that dies mid-delivery releases its rows for someone else rather than
    losing them.
    """
    delivered, failed, dead = 0, 0, 0
    conn.execute("BEGIN")
    try:
        rows = conn.execute(
            "SELECT * FROM outbox WHERE relayed_at IS NULL AND attempts < ? "
            "ORDER BY seq FOR UPDATE SKIP LOCKED LIMIT ?",
            (MAX_ATTEMPTS, batch),
        ).fetchall()
        for row in rows:
            event = Event(
                {
                    "event_id": row["event_id"],
                    "type": row["type"],
                    "aggregate_id": row["aggregate_id"],
                    "payload": json.loads(row["payload"]),
                    "created_at": row["created_at"],
                    "seq": row["seq"],
                }
            )
            errors = []
            for subscriber in list(SUBSCRIBERS.values()):
                try:
                    subscriber.on_event(event)
                except Exception as exc:
                    errors.append(f"{subscriber.name}: {exc}")
            if errors:
                attempts = int(row["attempts"]) + 1
                conn.execute(
                    "UPDATE outbox SET attempts=?, last_error=? WHERE seq=?",
                    (attempts, "; ".join(errors)[:500], row["seq"]),
                )
                failed += 1
                if attempts >= MAX_ATTEMPTS:
                    dead += 1
            else:
                conn.execute(
                    "UPDATE outbox SET relayed_at=?, attempts=? WHERE seq=?",
                    (now_iso(), int(row["attempts"]) + 1, row["seq"]),
                )
                delivered += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"claimed": len(rows), "delivered": delivered, "failed": failed, "dead_lettered": dead}


def pending(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) FILTER (WHERE relayed_at IS NULL AND attempts < "
        f"{MAX_ATTEMPTS}) AS waiting,"
        f" COUNT(*) FILTER (WHERE relayed_at IS NULL AND attempts >= {MAX_ATTEMPTS}) AS dead,"
        " COUNT(*) FILTER (WHERE relayed_at IS NOT NULL) AS delivered FROM outbox"
    ).fetchone()
    return dict(row)


def run_forever(conn, *, every_s: float = 1.0, max_passes: int | None = None) -> None:
    """Foreground worker. Run as many as you like against one database."""
    passes = 0
    while max_passes is None or passes < max_passes:
        result = drain(conn)
        if result["delivered"] or result["failed"]:
            print(
                f"[{now_iso()}] delivered={result['delivered']} "
                f"failed={result['failed']} dead={result['dead_lettered']}"
            )
        passes += 1
        if max_passes is not None and passes >= max_passes:
            break
        time.sleep(every_s)
