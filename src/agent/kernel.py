"""The enforcement path: gate, verify, and the purchase saga.

MOCK BOUNDARY (Dev 2's lane) -- but the decision table is the real one, because
a mock that approves everything is forbidden and would hide the only failures
worth testing.

The two properties this file exists to hold:

  S1  No model runs here.  Same input, same answer, every time.  `gate()` is a
      pure function of (claims, intent, offer, spend) -- no clock reads that
      are not passed in, no network, no LLM.
  S4  Nothing from the agent's memory or ontology is an input.  The gate sees
      the signed mandate and the numbers in the database.  That is all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from . import audit, jsonlogic
from .config import INTENT_TTL_S
from .crypto import jws
from .crypto.money import dec, fmt
from .ids import new_id, nonce, now_iso, now_ts

APPROVED, ESCALATED, REJECTED = "APPROVED", "ESCALATED", "REJECTED"


def _parse_json(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, (str, bytes, bytearray)):
        return json.loads(val)
    return val


@dataclass
class Decision:
    decision: str
    reason_code: str | None = None
    detail: str = ""
    diff: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "diff": self.diff,
        }


# ── the gate: pure, deterministic, no I/O ────────────────────────────────────
def gate(*, claims: dict, intent: dict, offer: dict, spend: dict, now: int) -> Decision:
    """Check 3 of figure 3: is this purchase inside the mandate?

    Checks 1 and 2 (agent identity, mandate alive) run before this in `verify`,
    because a forged agent or a dead mandate is not a judgement call and there is
    nothing to ask a human about.  Only this check may escalate (H4).
    """
    amount = dec(intent["amount"])
    limits = claims["limits"]

    # scope -- not a judgement call, these are refusals
    scope = claims.get("scope", {})
    if scope.get("categories") and offer["category"] not in scope["categories"]:
        return Decision(
            REJECTED, "CATEGORY_FORBIDDEN", f"{offer['category']} is not in {scope['categories']}"
        )
    if scope.get("merchants") and offer["merchant_id"] not in scope["merchants"]:
        return Decision(
            REJECTED,
            "MERCHANT_NOT_ALLOWED",
            f"{offer['merchant_id']} is not in {scope['merchants']}",
        )

    # currency
    if intent["currency"] != claims.get("currency", "USD"):
        return Decision(
            REJECTED,
            "CONDITION_FAILED",
            f"currency {intent['currency']} != {claims.get('currency')}",
        )

    # count limit -- exhausted is a refusal, not a question
    max_txn = limits.get("max_txn") or {}
    if max_txn.get("count") is not None and spend["txn_count"] >= int(max_txn["count"]):
        return Decision(
            REJECTED,
            "LIMIT_EXHAUSTED",
            f"{spend['txn_count']} of {max_txn['count']} purchases used",
        )

    # budget -- the whole remaining budget cannot cover it, so asking is pointless
    committed = dec(spend["spent_total"]) + dec(spend["reserved_amount"])
    remaining = dec(limits["total_budget"]) - committed
    if amount > remaining:
        return Decision(
            REJECTED,
            "BUDGET_EXCEEDED",
            f"{fmt(amount)} exceeds remaining budget {fmt(remaining)}",
            {"amount": fmt(amount), "remaining_budget": fmt(remaining)},
        )

    # per-transaction limit -- a judgement call, so a human gets asked (H4)
    if amount > dec(limits["max_per_txn"]):
        return Decision(
            ESCALATED,
            "AMOUNT_EXCEEDS_PER_TXN",
            f"{fmt(amount)} is over the {limits['max_per_txn']} per-purchase limit",
            {
                "amount": fmt(amount),
                "limit": limits["max_per_txn"],
                "over_by": fmt(amount - dec(limits["max_per_txn"])),
                "offer": offer["title"],
            },
        )

    # the buyer's own conditions, as signed (S5)
    context = {"offer": {**offer, "price": fmt(offer["price"])}, "now": now}
    try:
        passes = jsonlogic.evaluate(claims.get("conditions") or True, context)
    except jsonlogic.RuleError as exc:
        return Decision(REJECTED, "CONDITION_FAILED", f"unevaluable condition: {exc}")
    if not passes:
        return Decision(
            ESCALATED,
            "CONDITION_FAILED",
            f"fails the buyer's condition: {jsonlogic.describe(claims['conditions'])}",
            {
                "condition": jsonlogic.describe(claims["conditions"]),
                "offer_price": fmt(offer["price"]),
                "offer": offer["title"],
            },
        )

    return Decision(APPROVED)


# ── verify: identity, liveness, then an atomic reservation ───────────────────
def verify(
    conn, *, mandate_token: str, intent: dict, intent_sig: str, reserve: bool = True
) -> dict[str, Any]:
    """Runs the three checks in order and reserves budget in one guarded write."""
    from . import mandate as mandate_mod

    # CHECK 1 -- is the agent who it says it is? (C1, C2, C3, C5)
    try:
        claims = mandate_mod.verify_token(mandate_token)
    except jws.BadSignature as exc:
        return _refuse(conn, None, "INVALID_SIGNATURE", str(exc))
    try:
        jws.verify_detached(intent_sig, intent, mandate_mod.agent_key_from_mandate(claims))
    except jws.BadSignature as exc:
        return _refuse(conn, claims["jti"], "INVALID_PROOF_OF_POSSESSION", str(exc))
    if intent.get("mandate_jti") != claims["jti"]:
        return _refuse(
            conn, claims["jti"], "INVALID_PROOF_OF_POSSESSION", "intent does not name this mandate"
        )

    # freshness and replay (C6, C7)
    now = now_ts()
    if int(intent["exp"]) - int(intent["iat"]) > INTENT_TTL_S:
        return _refuse(
            conn, claims["jti"], "INVALID_SIGNATURE", f"intent lifetime over {INTENT_TTL_S}s"
        )
    if now > int(intent["exp"]):
        return _refuse(conn, claims["jti"], "INVALID_SIGNATURE", "intent expired")
    seen = conn.execute(
        "SELECT 1 FROM purchase_intents WHERE jti=? OR nonce=?", (intent["jti"], intent["nonce"])
    ).fetchone()
    if seen:
        return _refuse(conn, claims["jti"], "DUPLICATE_JTI", "intent id or nonce reused")

    # CHECK 2 -- is the mandate still alive? Read INSIDE this transaction (M9).
    row = conn.execute("SELECT * FROM mandates WHERE jti=?", (claims["jti"],)).fetchone()
    if row is None:
        return _refuse(conn, claims["jti"], "MANDATE_REVOKED", "unknown mandate")
    if row["status"] == "revoked":
        return _refuse(conn, claims["jti"], "MANDATE_REVOKED", "revoked by the holder")
    for ancestor in chain(conn, claims["jti"])[1:]:
        arow = conn.execute("SELECT status FROM mandates WHERE jti=?", (ancestor,)).fetchone()
        if arow and arow["status"] != "active":
            return _refuse(
                conn,
                claims["jti"],
                "MANDATE_REVOKED",
                f"parent mandate {ancestor} is {arow['status']}",
            )
    if row["status"] == "suspended":
        return _refuse(conn, claims["jti"], "MANDATE_SUSPENDED", "suspended")
    if row["status"] == "exhausted":
        return _refuse(conn, claims["jti"], "MANDATE_EXHAUSTED", "no budget left")
    if now < int(claims["nbf"]):
        return _refuse(conn, claims["jti"], "MANDATE_NOT_YET_VALID", "not valid yet")
    if now > int(claims["exp"]):
        conn.execute("UPDATE mandates SET status='expired' WHERE jti=?", (claims["jti"],))
        return _refuse(conn, claims["jti"], "MANDATE_EXPIRED", "past its validity window")

    # CHECK 3 -- is the purchase inside the mandate?
    # The offer is re-fetched from the merchant that issued it, so the price the
    # gate checks is the merchant's live price, not whatever the agent carried.
    from .ports.base import merchant_for

    try:
        offer = merchant_for(intent["merchant_id"]).get(conn, intent["offer_id"])
    except KeyError:
        return _refuse(
            conn,
            claims["jti"],
            "MERCHANT_NOT_ALLOWED",
            f"no merchant registered as {intent['merchant_id']!r}",
        )
    if offer is None:
        return _refuse(conn, claims["jti"], "RAIL_ERROR", "offer withdrawn")
    if fmt(intent["amount"]) != fmt(offer["price"]):
        return _refuse(
            conn,
            claims["jti"],
            "AMOUNT_MISMATCH",
            f"intent {intent['amount']} != catalog {offer['price']}",
        )

    spend = {
        "spent_total": row["spent_total"],
        "reserved_amount": row["reserved_amount"],
        "txn_count": row["txn_count"],
    }
    decision = gate(claims=claims, intent=intent, offer=offer, spend=spend, now=now)

    if decision.decision != APPROVED or not reserve:
        audit.append(
            conn,
            "purchase.gated",
            {"intent_jti": intent["jti"], **decision.as_dict()},
            agent_id=claims["agent"],
            mandate_jti=claims["jti"],
        )
        return {**decision.as_dict(), "mandate_jti": claims["jti"]}

    # Atomic reservation across the whole ancestry (M2, M9).
    # Zero rows updated means something moved underneath us -- refuse, never guess.
    if not reserve_chain(conn, claims["jti"], intent["amount"]):
        return _refuse(
            conn,
            claims["jti"],
            "BUDGET_EXCEEDED",
            "mandate state changed during the check, or an ancestor mandate cannot cover it",
        )

    reservation_id = new_id("rsv")
    conn.execute(
        "INSERT INTO purchase_intents(jti,mandate_jti,agent_id,nonce,intent,signature,"
        "status,created_at) VALUES(?,?,?,?,?,?,'reserved',?)",
        (
            intent["jti"],
            claims["jti"],
            claims["agent"],
            intent["nonce"],
            json.dumps(intent),
            intent_sig,
            now_iso(),
        ),
    )
    audit.append(
        conn,
        "purchase.verified",
        {
            "intent_jti": intent["jti"],
            "reservation_id": reservation_id,
            "amount": intent["amount"],
            "offer_id": intent["offer_id"],
        },
        agent_id=claims["agent"],
        mandate_jti=claims["jti"],
    )
    return {
        "decision": APPROVED,
        "reason_code": None,
        "detail": "",
        "diff": {},
        "reservation_id": reservation_id,
        "mandate_jti": claims["jti"],
    }


def _refuse(conn, mandate_jti: str | None, code: str, detail: str) -> dict[str, Any]:
    audit.append(
        conn, "purchase.refused", {"reason_code": code, "detail": detail}, mandate_jti=mandate_jti
    )
    return {"decision": REJECTED, "reason_code": code, "detail": detail, "diff": {}}


def chain(conn, mandate_jti: str) -> list[str]:
    """A mandate and every mandate it was derived from, child first.

    A sticky approval issues a mini-mandate with `parent_jti` (H6).  If that
    child kept its own separate budget, approving one over-limit purchase would
    quietly mint new spending power -- the approval would become an escape hatch
    rather than a one-off exception.  So a child reserves, settles and releases
    against its whole ancestry: it can never spend what its parent cannot.
    """
    seen: list[str] = []
    current: str | None = mandate_jti
    while current and current not in seen:
        seen.append(current)
        row = conn.execute("SELECT parent_jti FROM mandates WHERE jti=?", (current,)).fetchone()
        current = row["parent_jti"] if row else None
    return seen


def reserve_chain(conn, mandate_jti: str, amount: str) -> bool:
    """Compare-and-swap up the chain. All or nothing (M2)."""
    done: list[str] = []
    for jti in chain(conn, mandate_jti):
        row = conn.execute("SELECT * FROM mandates WHERE jti=?", (jti,)).fetchone()
        if row is None or row["status"] != "active":
            _unreserve(conn, done, amount)
            return False
        claims = _parse_json(row["claims"])
        committed = dec(row["spent_total"]) + dec(row["reserved_amount"]) + dec(amount)
        if committed > dec(claims["limits"]["total_budget"]):
            _unreserve(conn, done, amount)
            return False
        cur = conn.execute(
            "UPDATE mandates SET reserved_amount=?, updated_at=? "
            "WHERE jti=? AND status='active' AND reserved_amount=? AND spent_total=? "
            "AND txn_count=?",
            (
                fmt(dec(row["reserved_amount"]) + dec(amount)),
                now_iso(),
                jti,
                row["reserved_amount"],
                row["spent_total"],
                row["txn_count"],
            ),
        )
        if cur.rowcount == 0:
            _unreserve(conn, done, amount)
            return False
        done.append(jti)
    return True


def _unreserve(conn, jtis: list[str], amount: str) -> None:
    for jti in jtis:
        row = conn.execute("SELECT reserved_amount FROM mandates WHERE jti=?", (jti,)).fetchone()
        if row:
            freed = max(Decimal("0"), dec(row["reserved_amount"]) - dec(amount))
            conn.execute("UPDATE mandates SET reserved_amount=? WHERE jti=?", (fmt(freed), jti))


def release(conn, mandate_jti: str, amount: str) -> None:
    """Give the reservation back, all the way up (M6). Compensation, not deletion."""
    _unreserve(conn, chain(conn, mandate_jti), amount)


def settle(conn, mandate_jti: str, amount: str) -> None:
    """Reservation becomes spend, exactly once, on the child and every ancestor."""
    for jti in chain(conn, mandate_jti):
        row = conn.execute("SELECT * FROM mandates WHERE jti=?", (jti,)).fetchone()
        if row is None:
            continue
        freed = max(Decimal("0"), dec(row["reserved_amount"]) - dec(amount))
        spent = dec(row["spent_total"]) + dec(amount)
        count = int(row["txn_count"]) + 1
        claims = _parse_json(row["claims"])
        exhausted = spent >= dec(claims["limits"]["total_budget"])
        conn.execute(
            "UPDATE mandates SET reserved_amount=?, spent_total=?, txn_count=?, status=?,"
            " updated_at=? WHERE jti=?",
            (
                fmt(freed),
                fmt(spent),
                count,
                "exhausted" if exhausted else row["status"],
                now_iso(),
                jti,
            ),
        )


def build_intent(*, mandate_jti: str, agent_id: str, offer: dict) -> dict[str, Any]:
    """The canonical purchase intent (schemas.md section 2).

    The amount is copied from the offer, never chosen -- S6 is structural here,
    not a validation step.
    """
    issued = now_ts()
    return {
        "typ": "purchase_intent_v1",
        "mandate_jti": mandate_jti,
        "agent": agent_id,
        "merchant_id": offer["merchant_id"],
        "offer_id": offer["offer_id"],
        "amount": fmt(offer["price"]),
        "currency": offer["currency"],
        "nonce": nonce(),
        "jti": new_id("int"),
        "iat": issued,
        "exp": issued + INTENT_TTL_S,
    }


def submit_purchase(
    conn,
    *,
    offer_id: str,
    mandate_jti: str,
    merchant_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """The saga, as the MCP tool `request_purchase` triggers it.

    Order: build intent -> sign -> verify (gate + reserve) -> merchant checkout
    -> settle or compensate.  There is no branch that reaches the rail without
    passing verify (S2).
    """
    from . import mandate as mandate_mod
    from .ports.base import merchant_for
    from .registry import agent_private_key

    mandate_row = mandate_mod.get(conn, mandate_jti)
    claims = _parse_json(mandate_row["claims"])
    try:
        port = merchant_for(merchant_id)
    except (KeyError, StopIteration):
        return {
            "status": "rejected",
            "reason_code": "MERCHANT_NOT_ALLOWED",
            "detail": f"no merchant registered as {merchant_id!r}",
        }
    offer = port.get(conn, offer_id)
    if offer is None:
        return {
            "status": "rejected",
            "reason_code": "RAIL_ERROR",
            "detail": f"no such offer {offer_id} at {port.merchant_id}",
        }

    intent = build_intent(mandate_jti=mandate_jti, agent_id=claims["agent"], offer=offer)
    signature = jws.sign_detached(
        intent, agent_private_key(claims["agent"]), kid=claims["agent"], typ="purchase-intent+jws"
    )

    purchase_id = new_id("pur")
    stamp = now_iso()
    conn.execute(
        "INSERT INTO purchases(id,mandate_jti,intent_jti,status,amount,created_at,"
        "updated_at) VALUES(?,?,?,'pending',?,?,?)",
        (purchase_id, mandate_jti, intent["jti"], intent["amount"], stamp, stamp),
    )
    audit.append(
        conn,
        "purchase.requested",
        {
            "purchase_id": purchase_id,
            "intent_jti": intent["jti"],
            "offer_id": offer_id,
            "amount": intent["amount"],
            "merchant_id": port.merchant_id,
        },
        agent_id=claims["agent"],
        run_id=run_id,
        mandate_jti=mandate_jti,
    )

    decision = verify(conn, mandate_token=mandate_row["token"], intent=intent, intent_sig=signature)

    if decision["decision"] == ESCALATED:
        conn.execute(
            "UPDATE purchases SET status='awaiting_escalation', reason_code=?,"
            " updated_at=? WHERE id=?",
            (decision["reason_code"], now_iso(), purchase_id),
        )
        from .escalation import open_escalation

        esc = open_escalation(
            conn,
            purchase_id=purchase_id,
            mandate_jti=mandate_jti,
            run_id=run_id,
            diff=decision.get("diff", {}),
            reason_code=decision["reason_code"],
        )
        return {
            "status": "escalated",
            "purchase_id": purchase_id,
            "escalation_id": esc["id"],
            "reason_code": decision["reason_code"],
            "detail": decision["detail"],
            "diff": decision.get("diff", {}),
            "intent": intent,
            "signature": signature,
        }

    if decision["decision"] != APPROVED:
        conn.execute(
            "UPDATE purchases SET status='rejected', reason_code=?, updated_at=? WHERE id=?",
            (decision["reason_code"], now_iso(), purchase_id),
        )
        audit.append(
            conn,
            "purchase.rejected",
            {
                "purchase_id": purchase_id,
                "reason_code": decision["reason_code"],
                "detail": decision["detail"],
            },
            agent_id=claims["agent"],
            run_id=run_id,
            mandate_jti=mandate_jti,
        )
        return {
            "status": "rejected",
            "purchase_id": purchase_id,
            "reason_code": decision["reason_code"],
            "detail": decision["detail"],
        }

    return _charge(
        conn,
        purchase_id=purchase_id,
        mandate_row=mandate_row,
        claims=claims,
        intent=intent,
        signature=signature,
        decision=decision,
        run_id=run_id,
    )


def _charge(
    conn, *, purchase_id, mandate_row, claims, intent, signature, decision, run_id
) -> dict[str, Any]:
    from .mocks.rail import RailError
    from .ports.base import merchant_for

    conn.execute(
        "UPDATE purchases SET status='charging', reservation_id=?, updated_at=? WHERE id=?",
        (decision.get("reservation_id"), now_iso(), purchase_id),
    )

    def live_state():
        """Re-read mandate state at settlement time. A cached answer here is
        exactly the time-of-check-to-time-of-use hole revocation must not have."""
        row = conn.execute("SELECT status FROM mandates WHERE jti=?", (claims["jti"],)).fetchone()
        if row and row["status"] == "active":
            return {"decision": APPROVED, "reservation_id": decision.get("reservation_id")}
        return {"decision": REJECTED, "reason_code": "MANDATE_REVOKED"}

    port = merchant_for(intent["merchant_id"])
    offer = port.get(conn, intent["offer_id"]) or {
        "offer_id": intent["offer_id"],
        "title": intent["offer_id"],
    }

    capture = None
    if getattr(port, "kernel_capture", False):

        def capture(*, amount: str, cart_hash: str) -> str:
            """Decision 0030: the kernel issuer key mints the capture token
            binding purchase, reservation, quoted total and cart. Reaching
            this call path IS the released approval; the bridge re-verifies
            the token, the drift and the cap before its single click.
            TT_RAPPI_CAPTURE_DRY_RUN=0 mints a live-capture token."""
            import os

            from src.api.decision.capture_token import mint_capture_token
            from src.api.services.keys import key_store

            issuer = key_store.issuer_key()
            return mint_capture_token(
                # the token binds the approved INTENT id: it is what the
                # adapter sends as body purchase_id, and the bridge requires
                # body == token on that field
                purchase_id=str(intent["jti"]),
                reservation_id=decision.get("reservation_id"),
                amount=amount,
                cart_hash=cart_hash,
                key=issuer.key,
                kid=issuer.kid,
                ttl_seconds=120,  # == stepup window: the approval IS the token
                dry_run=os.environ.get("TT_RAPPI_CAPTURE_DRY_RUN", "1").strip().lower()
                not in ("0", "false"),
            )

    try:
        result = port.settle(
            conn,
            offer=offer,
            mandate_claims=claims,
            mandate_token=mandate_row["token"],
            intent=intent,
            signature=signature,
            verify_fn=live_state,
            capture=capture,
        )
    except RailError as exc:
        return _compensate(conn, purchase_id, claims, intent, exc.code, str(exc), run_id)
    except Exception as exc:
        return _compensate(conn, purchase_id, claims, intent, "RAIL_ERROR", str(exc)[:200], run_id)

    if not result.get("accepted"):
        return _compensate(
            conn,
            purchase_id,
            claims,
            intent,
            result.get("reason_code") or "RAIL_ERROR",
            result.get("detail", ""),
            run_id,
        )

    settle(conn, claims["jti"], intent["amount"])
    conn.execute(
        "UPDATE purchases SET status='captured', receipt=?, updated_at=? WHERE id=?",
        (json.dumps(result["receipt"]), now_iso(), purchase_id),
    )
    conn.execute("UPDATE purchase_intents SET status='captured' WHERE jti=?", (intent["jti"],))
    audit.append(
        conn,
        "purchase.captured",
        {"purchase_id": purchase_id, "receipt": result["receipt"]},
        agent_id=claims["agent"],
        run_id=run_id,
        mandate_jti=claims["jti"],
    )
    return {"status": "captured", "purchase_id": purchase_id, "receipt": result["receipt"]}


def _compensate(conn, purchase_id, claims, intent, code, detail, run_id) -> dict:
    release(conn, claims["jti"], intent["amount"])
    conn.execute(
        "UPDATE purchases SET status='compensated', reason_code=?, updated_at=? WHERE id=?",
        (code, now_iso(), purchase_id),
    )
    audit.append(
        conn,
        "purchase.compensated",
        {"purchase_id": purchase_id, "reason_code": code, "detail": detail},
        agent_id=claims["agent"],
        run_id=run_id,
        mandate_jti=claims["jti"],
    )
    return {"status": "rejected", "purchase_id": purchase_id, "reason_code": code, "detail": detail}
