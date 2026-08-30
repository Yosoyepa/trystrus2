"""Outbox relay job entrypoint (`python -m src.api.jobs.relay`).

v0 scope: drain once per invocation (Cloud Run Jobs are Scheduler-driven),
at-least-once, SKIP LOCKED concurrency between job instances. Delivery to
evidence sinks (ledger mirror, signed webhook) lands with D2-I/I-6; today the
job logs drained events and optionally posts signed webhooks when
TT_WEBHOOK_URL is configured.

Secrets/env:
    DATABASE_URL   required (psycopg DSN, injected by Cloud Run)
    TT_WEBHOOK_URL optional (signed webhook sink)
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("aval.jobs.relay")


class _LogSink:
    """Minimal sink: log each event id so the Job output is inspectable."""

    def handle(self, event) -> None:  # noqa: ANN001 — duck-typed OutboxEvent
        logger.info(
            json.dumps(
                {
                    "message": "outbox.event.relayed",
                    "event_id": event.event_id,
                    "type": event.type,
                    "aggregate_id": event.aggregate_id,
                }
            )
        )


def build_relay():
    """Compose the relay from env (kept import-light for job startup)."""

    from src.api.events.relay import OutboxRelay, PostgresOutboxStore

    store = PostgresOutboxStore(dsn=os.environ["DATABASE_URL"])
    sinks: list = [_LogSink()]

    webhook_url = os.environ.get("TT_WEBHOOK_URL")
    if webhook_url:
        from src.api.audit.signer_local import LocalEd25519Signer
        from src.api.events.webhook_signed import SignedWebhookPoster

        sinks.insert(0, SignedWebhookPoster(webhook_url, LocalEd25519Signer()))

    return OutboxRelay(store, default_sinks=sinks)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    relay = build_relay()
    drained = relay.drain(limit=100)
    logger.info(
        json.dumps({"message": "outbox.drain.complete", "drained": drained})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
