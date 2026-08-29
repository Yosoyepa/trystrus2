"""Recurrent search: standing watches with thresholds a human sets.

A person says "buy it if a Cordoba seat drops below 125" and then closes their
laptop.  The watcher polls the catalog on an interval, evaluates the threshold,
and when it fires it goes through EXACTLY the same gate as a chat purchase.

Two rules this file must not break:

  S2  A watch is not a shortcut to money.  When it fires it calls
      `kernel.submit_purchase`, so the mandate, the limits and the signature
      checks all still apply.  A watch that could buy on its own would be a
      second path to the rail, and there is only ever one.
  §10 The watcher polls the REST catalog rather than MCP.  MCP is the
      interactive agent's tool surface; a background job does not need a model
      in the loop to compare a number to a threshold.

The threshold is JsonLogic, evaluated by the same restricted evaluator the
mandate uses -- so a human's standing order is as inspectable as their mandate.
"""
from __future__ import annotations
import json
import time
from typing import Any

from . import audit, jsonlogic, limits
from .crypto.money import fmt
from .ids import new_id, now_iso, now_ts
from .mocks import merchant


def create_watch(conn, *, agent_id: str, mandate_jti: str, query: dict,
                 threshold: dict, interval_s: int = 300, autobuy: bool = True,
                 created_by: str | None = None) -> dict[str, Any]:
    """`query` filters the catalog; `threshold` is the JsonLogic that must pass."""
    jsonlogic.evaluate(threshold, {"offer": {"price": "1.00"}, "now": now_ts()})
    # Guardrails before anything is persisted: a polling interval below the
    # floor and an unbounded number of watches are the two ways a standing
    # order becomes a denial-of-service against the merchant (and our bill).
    interval_s = limits.guard_watch_interval(int(interval_s))
    limits.guard_watch_count(conn, mandate_jti)
    watch_id = new_id("wch")
    conn.execute(
        "INSERT INTO watches(id,agent_id,mandate_jti,created_by,query,threshold,"
        "interval_s,autobuy,status,created_at) VALUES(?,?,?,?,?,?,?,?,'active',?)",
        (watch_id, agent_id, mandate_jti, created_by, json.dumps(query),
         json.dumps(threshold), int(interval_s), 1 if autobuy else 0, now_iso()))
    audit.append(conn, "watch.created",
                 {"watch_id": watch_id, "query": query,
                  "threshold": jsonlogic.describe(threshold),
                  "interval_s": interval_s, "autobuy": autobuy},
                 actor=created_by, agent_id=agent_id, mandate_jti=mandate_jti)
    return {"watch_id": watch_id, "threshold": jsonlogic.describe(threshold)}


def cancel(conn, watch_id: str, actor: str | None = None) -> None:
    conn.execute("UPDATE watches SET status='cancelled' WHERE id=?", (watch_id,))
    audit.append(conn, "watch.cancelled", {"watch_id": watch_id}, actor=actor)


def list_watches(conn, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM watches"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY created_at", args).fetchall()]


def _due(conn) -> list[Any]:
    rows = conn.execute("SELECT * FROM watches WHERE status='active'").fetchall()
    due = []
    for row in rows:
        if not row["last_checked_at"]:
            due.append(row)
            continue
        # coarse but honest: compare ISO strings via epoch
        import datetime as dt
        last = dt.datetime.fromisoformat(row["last_checked_at"])
        if (dt.datetime.now(dt.timezone.utc) - last).total_seconds() >= row["interval_s"]:
            due.append(row)
    return due


def check(conn, watch_id: str, *, force: bool = False) -> dict[str, Any]:
    """One poll of one watch. Idempotent-ish: a fired watch stops firing."""
    row = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such watch: {watch_id}")
    if row["status"] != "active" and not force:
        return {"watch_id": watch_id, "status": row["status"], "checked": False}

    limits.guard_merchant_call(conn, row["agent_id"])   # same budget as the agent
    query = json.loads(row["query"])
    threshold = json.loads(row["threshold"])
    offers = merchant.search_offers(
        conn, origin=query.get("origin"), destination=query.get("destination"),
        date=query.get("date"), category=query.get("category"))
    conn.execute("UPDATE watches SET last_checked_at=?, last_seen_price=? WHERE id=?",
                 (now_iso(), offers[0]["price"] if offers else None, watch_id))

    now = now_ts()
    matched = None
    for offer in offers:
        context = {"offer": {**offer, "price": fmt(offer["price"])}, "now": now}
        try:
            if jsonlogic.evaluate(threshold, context):
                matched = offer
                break
        except jsonlogic.RuleError:
            continue

    audit.append(conn, "watch.checked",
                 {"watch_id": watch_id, "offers_seen": len(offers),
                  "cheapest": offers[0]["price"] if offers else None,
                  "matched": matched["offer_id"] if matched else None},
                 agent_id=row["agent_id"], mandate_jti=row["mandate_jti"])

    cheapest = offers[0]["price"] if offers else None
    if not matched:
        return {"watch_id": watch_id, "checked": True, "matched": False,
                "cheapest": cheapest}

    audit.append(conn, "watch.fired",
                 {"watch_id": watch_id, "offer_id": matched["offer_id"],
                  "price": matched["price"],
                  "threshold": jsonlogic.describe(threshold)},
                 agent_id=row["agent_id"], mandate_jti=row["mandate_jti"])

    if not row["autobuy"]:
        conn.execute("UPDATE watches SET status='fired', fired_at=? WHERE id=?",
                     (now_iso(), watch_id))
        return {"watch_id": watch_id, "checked": True, "matched": True,
                "cheapest": cheapest, "offer": matched, "action": "notified"}

    # Fires into the same gate as everything else (S2).
    from . import kernel
    result = kernel.submit_purchase(conn, offer_id=matched["offer_id"],
                                    mandate_jti=row["mandate_jti"])
    if result["status"] in ("captured", "escalated"):
        conn.execute("UPDATE watches SET status='fired', fired_at=? WHERE id=?",
                     (now_iso(), watch_id))
    return {"watch_id": watch_id, "checked": True, "matched": True,
            "cheapest": cheapest, "offer": matched, "action": "purchase",
            "result": result}


def tick(conn) -> dict[str, Any]:
    """One pass: expire stale escalations, then poll every watch that is due.

    This is the unit a cron entry calls.  Sweeping escalations here is what makes
    the 120 s timeout real when nobody is looking at a terminal (H2, S3).
    """
    from . import escalation
    with limits.single_flight(conn, "watcher.tick") as acquired:
        if not acquired:
            # A previous tick is still running. Overlapping cron jobs are how a
            # one-minute schedule turns into a stampede; we skip, we do not queue.
            return {"at": now_iso(), "skipped": "another tick holds the lock",
                    "escalations_expired": 0, "watches_checked": 0, "fired": []}
        expired = escalation.sweep(conn)
        due = _due(conn)[: limits.QUOTA.max_watches_per_tick]
        results = []
        for row in due:
            try:
                results.append(check(conn, row["id"]))
            except limits.LimitExceeded as exc:
                audit.append(conn, "watch.throttled",
                             {"watch_id": row["id"], "reason_code": exc.code,
                              "detail": exc.detail}, agent_id=row["agent_id"])
                results.append({"watch_id": row["id"], "throttled": exc.code})
        return {"at": now_iso(), "escalations_expired": len(expired),
                "watches_checked": len(results),
                "throttled": [r for r in results if r.get("throttled")],
                "fired": [r for r in results if r.get("matched")]}


def run_forever(conn, *, every_s: int = 30, max_ticks: int | None = None) -> None:
    """Foreground daemon. `trytrust watch-daemon`. Ctrl-C to stop."""
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        summary = tick(conn)
        if summary["watches_checked"] or summary["escalations_expired"]:
            print(f"[{summary['at']}] checked={summary['watches_checked']} "
                  f"expired={summary['escalations_expired']} "
                  f"fired={len(summary['fired'])}")
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        time.sleep(every_s)


CRONTAB_HINT = """\
# TryTrust recurrent search -- one tick a minute is plenty for a demo.
# crontab -e, then:
* * * * * cd {repo} && /usr/bin/uv run python -m src.agent.cli tick >> {repo}/var/watcher.log 2>&1

# systemd timer alternative (var/trytrust-watch.timer + .service), or in GCP:
#   gcloud scheduler jobs create http trytrust-tick \\
#     --schedule="* * * * *" --uri="https://api.trytrust.lat/jobs/tick" \\
#     --oidc-service-account-email=...
"""
