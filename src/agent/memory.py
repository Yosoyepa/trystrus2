"""Transaction memory -- what this buyer normally does.

FOUNDATION names the gap this closes: the agent "needs access to past
transactions to know whether a purchase is a repeat and what it means".

The agent READS memory; it never owns it.  Memory is derived from the audit
chain, so an agent cannot rewrite its own past.  And by S4 none of this ever
reaches the gate: it makes proposals better, never permissions wider.
"""
from __future__ import annotations
import json
import sqlite3
from decimal import Decimal
from typing import Any

from .crypto.money import fmt


def recent_events(conn, mandate_jti: str, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT type,payload,created_at FROM audit_events WHERE mandate_jti=? "
        "ORDER BY seq DESC LIMIT ?", (mandate_jti, limit)).fetchall()
    return [{"type": r["type"], "at": r["created_at"], "payload": json.loads(r["payload"])}
            for r in rows]


def purchase_history(conn, mandate_jti: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT p.*, o.title, o.destination FROM purchases p "
        "LEFT JOIN purchase_intents i ON i.jti = p.intent_jti "
        "LEFT JOIN offers o ON o.id = json_extract(i.intent,'$.offer_id') "
        "WHERE p.mandate_jti=? ORDER BY p.created_at DESC LIMIT ?",
        (mandate_jti, limit)).fetchall()
    return [dict(r) for r in rows]


def summarise(conn, mandate_jti: str) -> dict[str, Any]:
    """A few honest numbers. No model involved -- this is arithmetic."""
    history = purchase_history(conn, mandate_jti, limit=100)
    captured = [h for h in history if h["status"] == "captured"]
    amounts = [Decimal(h["amount"]) for h in captured if h["amount"]]
    refusals: dict[str, int] = {}
    for h in history:
        if h["status"] in ("rejected", "compensated") and h["reason_code"]:
            refusals[h["reason_code"]] = refusals.get(h["reason_code"], 0) + 1
    destinations: dict[str, int] = {}
    for h in captured:
        if h.get("destination"):
            destinations[h["destination"]] = destinations.get(h["destination"], 0) + 1
    return {
        "purchases_made": len(captured),
        "total_spent": fmt(sum(amounts)) if amounts else "0.00",
        "typical_price": fmt(sum(amounts) / len(amounts)) if amounts else None,
        "cheapest_paid": fmt(min(amounts)) if amounts else None,
        "most_expensive_paid": fmt(max(amounts)) if amounts else None,
        "frequent_destinations": sorted(destinations, key=destinations.get, reverse=True)[:3],
        "recent_refusals": refusals,
        "is_repeat_buyer": len(captured) > 0,
    }


def render(summary: dict[str, Any]) -> str:
    if not summary["is_repeat_buyer"]:
        return "no completed purchases yet under this mandate"
    parts = [
        f"purchases so far: {summary['purchases_made']}",
        f"total spent: {summary['total_spent']}",
        f"typical price paid: {summary['typical_price']}",
    ]
    if summary["frequent_destinations"]:
        parts.append("usual destinations: " + ", ".join(summary["frequent_destinations"]))
    if summary["recent_refusals"]:
        parts.append("recent refusals: " + ", ".join(
            f"{k}x{v}" for k, v in summary["recent_refusals"].items()))
    return "\n".join(parts)
