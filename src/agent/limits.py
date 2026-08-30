"""Guardrails: rate limits, single-flight locks, spend caps, blast radius.

The first line of defence is structural and already holds: the model has no tool
that can create a watch, set a polling interval, spawn a run, or reach the
payment rail.  `request_purchase(offer_id, mandate_jti)` is the entire surface.
So "a malicious prompt tells it to poll the merchant every 0.01 s" is not a
thing the prompt can express -- there is no verb for it.

This file is the second line, for everything the first line does not cover: a
compromised console, a buggy loop of ours, a human who sets `--every 0`, two
cron ticks overlapping, a merchant that starts hanging, an LLM bill that runs
away overnight.  The rule it encodes:

    every loop is bounded, every external call is rate limited,
    every budget is counted, and exhaustion FAILS CLOSED.

Counters live in the database, so a restart does not reset an attacker's budget.
"""

from __future__ import annotations

import datetime as _dt
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .ids import now_iso


class LimitExceeded(Exception):
    """Raised when a guardrail trips. Carries a ReasonCode for the log."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Quota:
    """Every number here is a deliberate ceiling. Override with env vars."""

    # scheduling — the floor that makes "poll every 0.01s" unrepresentable
    min_watch_interval_s: int = _env_int("TT_MIN_WATCH_INTERVAL_S", 30)
    max_watches_per_mandate: int = _env_int("TT_MAX_WATCHES_PER_MANDATE", 10)
    max_watches_total: int = _env_int("TT_MAX_WATCHES_TOTAL", 200)
    max_watches_per_tick: int = _env_int("TT_MAX_WATCHES_PER_TICK", 25)

    # external calls, per agent, as a token bucket (rate per second, burst)
    merchant_calls_per_s: float = _env_float("TT_MERCHANT_CALLS_PER_S", 2.0)
    merchant_burst: int = _env_int("TT_MERCHANT_BURST", 20)
    llm_calls_per_s: float = _env_float("TT_LLM_CALLS_PER_S", 0.5)
    llm_burst: int = _env_int("TT_LLM_BURST", 10)

    # runs
    max_steps_per_run: int = _env_int("TT_MAX_STEPS_PER_RUN", 24)
    max_run_seconds: int = _env_int("TT_MAX_RUN_SECONDS", 120)
    max_runs_per_agent_hour: int = _env_int("TT_MAX_RUNS_PER_AGENT_HOUR", 60)

    # prompt shape — bounds cost and context, and caps what an injected
    # catalog can push into the model at once
    max_offers_in_prompt: int = _env_int("TT_MAX_OFFERS_IN_PROMPT", 12)
    max_offer_text_chars: int = _env_int("TT_MAX_OFFER_TEXT_CHARS", 400)
    max_ontology_chars: int = _env_int("TT_MAX_ONTOLOGY_CHARS", 4000)

    # money that is not the buyer's: our inference bill
    llm_calls_per_day: int = _env_int("TT_LLM_CALLS_PER_DAY", 2000)

    # humans are a scarce resource too
    max_escalations_per_hour: int = _env_int("TT_MAX_ESCALATIONS_PER_HOUR", 12)

    # locks
    tick_lock_ttl_s: int = _env_int("TT_TICK_LOCK_TTL_S", 55)


QUOTA = Quota()


# ── token bucket, persisted ──────────────────────────────────────────────────
def take(conn, key: str, *, rate: float, burst: int, cost: float = 1.0) -> None:
    """Consume one token or raise. Refills at `rate`/second up to `burst`."""
    now = _dt.datetime.now(_dt.UTC)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT tokens, updated_at FROM rate_buckets WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            tokens = float(burst)
        else:
            elapsed = (now - _dt.datetime.fromisoformat(row["updated_at"])).total_seconds()
            tokens = min(float(burst), row["tokens"] + max(0.0, elapsed) * rate)
        if tokens < cost:
            conn.execute("COMMIT")
            wait = (cost - tokens) / rate if rate > 0 else float("inf")
            raise LimitExceeded(
                "RATE_LIMITED",
                f"{key} is out of budget; {wait:.1f}s until the next call is allowed",
            )
        conn.execute(
            "INSERT INTO rate_buckets(key,tokens,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET tokens=excluded.tokens, "
            "updated_at=excluded.updated_at",
            (key, tokens - cost, now.isoformat()),
        )
        conn.execute("COMMIT")
    except LimitExceeded:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── rolling counters ─────────────────────────────────────────────────────────
def bump(conn, key: str, window: str, *, cap: int, amount: float = 1.0) -> None:
    """Increment a windowed counter and raise once it passes `cap`."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT value FROM counters WHERE key=? AND window_key=?", (key, window)
        ).fetchone()
        value = (row["value"] if row else 0.0) + amount
        if value > cap:
            conn.execute("COMMIT")
            raise LimitExceeded("QUOTA_EXHAUSTED", f"{key} used {value:.0f} of {cap} for {window}")
        conn.execute(
            "INSERT INTO counters(key,window_key,value,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key,window_key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (key, window, value, now_iso()),
        )
        conn.execute("COMMIT")
    except LimitExceeded:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise


def hour_window() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H")


def day_window() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")


# ── single flight ────────────────────────────────────────────────────────────
@contextmanager
def single_flight(conn, name: str, ttl_s: int | None = None) -> Iterator[bool]:
    """Only one holder at a time, across processes and machines.

    Cron overlap is the classic way a one-minute job becomes a thundering herd:
    a tick that takes ninety seconds under a one-minute schedule means two, then
    three, then a stampede. The second holder **skips** rather than queueing —
    a queued backlog is the stampede with extra steps.

    Postgres advisory locks replace the lock table this used to keep. They need
    no TTL because the lock dies with the session, so a crashed holder releases
    immediately instead of wedging the system until a timeout expires. They are
    also re-entrant *within* a session, which is why this opens a connection of
    its own: two calls must be two sessions, or a process would happily grant
    itself a lock it already holds.

    `ttl_s` is accepted and ignored, so existing callers keep working.
    """
    from . import db

    holder = db.connect()
    acquired = False
    try:
        acquired = bool(
            holder.execute("SELECT pg_try_advisory_lock(hashtext(?)) AS ok", (name,)).fetchone()[
                "ok"
            ]
        )
        yield acquired
    finally:
        if acquired:
            holder.execute("SELECT pg_advisory_unlock(hashtext(?))", (name,))
        holder.close()


def held_locks(conn) -> list[dict]:
    """What the control tower shows: advisory locks this database is holding."""
    return conn.execute(
        "SELECT objid AS lock_id, pid, granted FROM pg_locks "
        "WHERE locktype='advisory' ORDER BY objid"
    ).fetchall()


# ── the specific guards, each named after what it stops ──────────────────────
def guard_watch_interval(interval_s: int) -> int:
    """A polling interval below the floor is clamped, not accepted."""
    if interval_s < QUOTA.min_watch_interval_s:
        raise LimitExceeded(
            "INTERVAL_TOO_SMALL", f"{interval_s}s is below the {QUOTA.min_watch_interval_s}s floor"
        )
    return interval_s


def guard_watch_count(conn, mandate_jti: str) -> None:
    per = conn.execute(
        "SELECT COUNT(*) c FROM watches WHERE mandate_jti=? AND status='active'", (mandate_jti,)
    ).fetchone()["c"]
    if per >= QUOTA.max_watches_per_mandate:
        raise LimitExceeded("TOO_MANY_WATCHES", f"{mandate_jti} already has {per} active watches")
    total = conn.execute("SELECT COUNT(*) c FROM watches WHERE status='active'").fetchone()["c"]
    if total >= QUOTA.max_watches_total:
        raise LimitExceeded("TOO_MANY_WATCHES", f"{total} active watches system-wide")


def guard_merchant_call(conn, agent_id: str, mandate_jti: str | None = None) -> None:
    """Two buckets, both of which must have room.

    Keying only on the agent left a hole: whoever can mint agent ids gets a
    fresh budget with each one. The mandate is the thing an attacker cannot
    mint -- it is signed by a human -- so it carries the second ceiling.
    """
    take(conn, f"merchant:{agent_id}", rate=QUOTA.merchant_calls_per_s, burst=QUOTA.merchant_burst)
    if mandate_jti:
        take(
            conn,
            f"merchant:mandate:{mandate_jti}",
            rate=QUOTA.merchant_calls_per_s,
            burst=QUOTA.merchant_burst,
        )


def guard_llm_call(conn, agent_id: str, mandate_jti: str | None = None) -> None:
    take(conn, f"llm:{agent_id}", rate=QUOTA.llm_calls_per_s, burst=QUOTA.llm_burst)
    if mandate_jti:
        take(conn, f"llm:mandate:{mandate_jti}", rate=QUOTA.llm_calls_per_s, burst=QUOTA.llm_burst)
    bump(conn, "llm:calls", day_window(), cap=QUOTA.llm_calls_per_day)


def guard_run_start(conn, agent_id: str) -> None:
    bump(conn, f"runs:{agent_id}", hour_window(), cap=QUOTA.max_runs_per_agent_hour)


def guard_escalation(conn, approver_id: str | None) -> None:
    """Stops an escalation storm from drowning the one human who can say no."""
    bump(
        conn,
        f"escalations:{approver_id or 'unassigned'}",
        hour_window(),
        cap=QUOTA.max_escalations_per_hour,
    )


def clamp_offers(offers: list[dict]) -> list[dict]:
    """Bound what an injected catalog can push into the prompt at once."""
    trimmed = []
    for offer in offers[: QUOTA.max_offers_in_prompt]:
        copy = dict(offer)
        for field in ("title", "description"):
            if copy.get(field):
                copy[field] = str(copy[field])[: QUOTA.max_offer_text_chars]
        trimmed.append(copy)
    return trimmed


def clamp_text(text: str, limit: int | None = None) -> str:
    cap = QUOTA.max_ontology_chars if limit is None else limit
    return (text or "")[:cap]


def snapshot(conn) -> dict:
    """What the control tower shows: current budgets and locks."""
    return {
        "quota": QUOTA.__dict__,
        "buckets": [
            dict(r)
            for r in conn.execute(
                # `tokens` is DOUBLE PRECISION and Postgres has no
                # round(double precision, int) -- only round(numeric, int).
                # CAST, not `::`: the same SQL also runs on the SQLite test
                # fake, which cannot parse Postgres cast syntax.
                "SELECT key, ROUND(CAST(tokens AS numeric),2) tokens, updated_at "
                "FROM rate_buckets ORDER BY key"
            ).fetchall()
        ],
        "counters": [
            dict(r)
            for r in conn.execute(
                "SELECT key, window_key AS window, value FROM counters ORDER BY key, window_key"
            ).fetchall()
        ],
        "locks": [dict(r) for r in held_locks(conn)],
    }
