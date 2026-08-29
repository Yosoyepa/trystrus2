"""Human in the loop: a stop, not a notification (H1).

The contract that matters is the resume (schemas.md section 5): an approval
authorises a RETRY, never a bypass.  When a human says yes, the purchase goes
back through the gate, because the world may have moved while we waited -- the
mandate could have been revoked in those ninety seconds, and an approval that
skipped the gate would happily charge a dead mandate.

Silence is not consent.  The timeout denies (H2, S3).
"""
from __future__ import annotations
import datetime as _dt
import json
from typing import Any

from . import audit, limits
from .config import ESCALATION_TIMEOUT_S
from .crypto import jws
from .crypto.keys import load_or_create
from .ids import new_id, now_iso


def open_escalation(conn, *, purchase_id: str, mandate_jti: str, run_id: str | None,
                    diff: dict, reason_code: str) -> dict[str, Any]:
    row = conn.execute("SELECT agent_id FROM mandates WHERE jti=?", (mandate_jti,)).fetchone()
    agent_id = row["agent_id"] if row else None
    approver = None
    if agent_id:
        arow = conn.execute("SELECT approver_id FROM agents WHERE id=?", (agent_id,)).fetchone()
        approver = arow["approver_id"] if arow else None  # H5: a person, not a channel

    # One human can only answer so many questions. An agent that escalates in a
    # loop is a broken agent, and drowning the approver is how a real approval
    # gets rubber-stamped.
    limits.guard_escalation(conn, approver)
    esc_id = new_id("esc")
    timeout_at = (_dt.datetime.now(_dt.timezone.utc)
                  + _dt.timedelta(seconds=ESCALATION_TIMEOUT_S)).replace(
                      microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO escalations(id,purchase_id,mandate_jti,run_id,approver_id,status,"
        "diff,timeout_at,created_at) VALUES(?,?,?,?,?,'pending',?,?,?)",
        (esc_id, purchase_id, mandate_jti, run_id, approver,
         json.dumps({**diff, "reason_code": reason_code}), timeout_at, now_iso()))
    audit.append(conn, "purchase.escalated",
                 {"purchase_id": purchase_id, "escalation_id": esc_id,
                  "reason_code": reason_code, "diff": diff, "approver_id": approver,
                  "timeout_at": timeout_at},
                 agent_id=agent_id, run_id=run_id, mandate_jti=mandate_jti)
    return {"id": esc_id, "timeout_at": timeout_at, "approver_id": approver,
            "diff": diff, "reason_code": reason_code}


def get(conn, esc_id: str):
    row = conn.execute("SELECT * FROM escalations WHERE id=?", (esc_id,)).fetchone()
    if row is None:
        raise KeyError(f"no such escalation: {esc_id}")
    return row


def is_expired(conn, esc_id: str) -> bool:
    row = get(conn, esc_id)
    if row["status"] != "pending":
        return False
    return now_iso() > row["timeout_at"]


def resolve(conn, esc_id: str, *, decision: str, approver: str,
            channel: str = "chat", sticky: bool = False) -> dict[str, Any]:
    """APPROVE or REJECT. Approving does NOT charge -- it re-enters the gate."""
    if decision not in ("APPROVE", "REJECT"):
        raise ValueError("decision must be APPROVE or REJECT")
    row = get(conn, esc_id)
    if row["status"] != "pending":
        # M5: resolving twice is a no-op, not a second charge.
        return {"escalation_id": esc_id, "status": row["status"],
                "decision": row["decision"], "idempotent_replay": True}

    if now_iso() > row["timeout_at"]:
        return expire(conn, esc_id)

    receipt = {"escalation_id": esc_id, "purchase_id": row["purchase_id"],
               "decision": decision, "approver": approver, "channel": channel,
               "at": now_iso()}
    receipt_sig = jws.sign_detached(receipt, load_or_create("issuer"), kid="v1",
                                    typ="approval-receipt+jws")
    conn.execute(
        "UPDATE escalations SET status='resolved', decision=?, approver=?, channel=?,"
        " receipt_sig=? WHERE id=?",
        (decision, approver, channel, receipt_sig, esc_id))
    audit.append(conn, "escalation.resolved",
                 {"escalation_id": esc_id, "purchase_id": row["purchase_id"],
                  "decision": decision, "approver": approver, "channel": channel,
                  "receipt_sig": receipt_sig},
                 actor=approver, run_id=row["run_id"], mandate_jti=row["mandate_jti"])

    if decision == "REJECT":
        conn.execute("UPDATE purchases SET status='rejected', reason_code=?, updated_at=?"
                     " WHERE id=?",
                     ("HUMAN_REJECTED", now_iso(), row["purchase_id"]))
        return {"escalation_id": esc_id, "decision": "REJECT", "status": "resolved",
                "outcome": {"status": "rejected", "reason_code": "HUMAN_REJECTED"}}

    outcome = _retry_through_gate(conn, row, approver, sticky=sticky)
    return {"escalation_id": esc_id, "decision": "APPROVE", "status": "resolved",
            "outcome": outcome}


def expire(conn, esc_id: str) -> dict[str, Any]:
    """Timeout: fail closed. Nothing is ever approved by silence (S3, H2)."""
    row = get(conn, esc_id)
    if row["status"] != "pending":
        return {"escalation_id": esc_id, "status": row["status"]}
    conn.execute("UPDATE escalations SET status='expired', decision='TIMEOUT' WHERE id=?",
                 (esc_id,))
    conn.execute("UPDATE purchases SET status='rejected', reason_code=?, updated_at=?"
                 " WHERE id=?",
                 ("ESCALATION_TIMEOUT_DENIED", now_iso(), row["purchase_id"]))
    audit.append(conn, "escalation.expired",
                 {"escalation_id": esc_id, "purchase_id": row["purchase_id"],
                  "reason_code": "ESCALATION_TIMEOUT_DENIED"},
                 run_id=row["run_id"], mandate_jti=row["mandate_jti"])
    return {"escalation_id": esc_id, "status": "expired",
            "outcome": {"status": "rejected", "reason_code": "ESCALATION_TIMEOUT_DENIED"}}


def _retry_through_gate(conn, row, approver: str, *, sticky: bool) -> dict[str, Any]:
    """S7: the approval buys a retry. The gate runs again, from scratch.

    `sticky` issues a derived mini-mandate with a widened per-transaction limit
    and `parent_jti` set (H6) -- the human raised the ceiling deliberately, and
    that new permission is itself a signed object, not a flag in a session.
    """
    from . import kernel, mandate as mandate_mod

    purchase = conn.execute("SELECT * FROM purchases WHERE id=?",
                            (row["purchase_id"],)).fetchone()
    intent_row = conn.execute("SELECT * FROM purchase_intents WHERE jti=?",
                              (purchase["intent_jti"],)).fetchone()
    offer_id = json.loads(intent_row["intent"])["offer_id"] if intent_row else None
    if offer_id is None:
        # the intent was never persisted (it never reserved), rebuild from the purchase
        ev = conn.execute(
            "SELECT payload FROM audit_events WHERE type='purchase.requested' "
            "AND payload LIKE ? ORDER BY seq DESC LIMIT 1",
            (f'%{row["purchase_id"]}%',)).fetchone()
        offer_id = json.loads(ev["payload"])["offer_id"] if ev else None
    if offer_id is None:
        return {"status": "rejected", "reason_code": "RAIL_ERROR",
                "detail": "cannot rebuild the intent to retry"}

    mandate_jti = row["mandate_jti"]
    if sticky:
        parent = mandate_mod.get(conn, mandate_jti)
        claims = json.loads(parent["claims"])
        amount = purchase["amount"]
        derived = mandate_mod.issue(
            conn, user_id=parent["user_id"], agent_id=parent["agent_id"],
            agent_jwk=claims["cnf"]["jwk"],
            payment_method_ref=claims["payment_method_ref"],
            scope=claims["scope"], conditions=True,
            limits={"max_per_txn": amount, "total_budget": amount,
                    "max_txn": {"count": 1, "period": "once"}},
            validity=claims["validity"], currency=claims["currency"],
            parent_jti=mandate_jti, signed_with=f"approval:{approver}")
        mandate_jti = derived["jti"]

    return kernel.submit_purchase(conn, offer_id=offer_id, mandate_jti=mandate_jti,
                                  run_id=row["run_id"])


def sweep(conn) -> list[dict[str, Any]]:
    """Expire everything past its deadline. The cron calls this."""
    rows = conn.execute(
        "SELECT id FROM escalations WHERE status='pending' AND timeout_at < ?",
        (now_iso(),)).fetchall()
    return [expire(conn, r["id"]) for r in rows]


def pending(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM escalations WHERE status='pending' ORDER BY created_at").fetchall()]
