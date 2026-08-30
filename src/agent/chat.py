"""Chat: a person asks, the agent works, the person can interrupt.

The whole point of a mandate is that the agent keeps going without a human --
right up to the moment it must not.  So a chat session has three shapes of turn:

  1. a request            -> start a run
  2. approve / reject     -> resolve the escalation the run is parked on (H1)
  3. anything else        -> guidance: replan with what the human just said

Case 3 is the interesting one.  Feedback arriving while a run is parked is not
an approval; it rejects the pending escalation and replans, because "find
something cheaper" means no to THIS purchase and yes to looking again.
"""

from __future__ import annotations

import re
from typing import Any

from . import escalation, graph
from .ids import new_id, now_iso

APPROVE_WORDS = {
    "approve",
    "approved",
    "yes",
    "y",
    "ok",
    "okay",
    "go",
    "go ahead",
    "do it",
    "buy it",
    "confirm",
    "confirmed",
    "si",
    "sí",
    "dale",
    "aprobar",
    "apruebo",
    "adelante",
}
REJECT_WORDS = {
    "reject",
    "rejected",
    "no",
    "n",
    "cancel",
    "stop",
    "deny",
    "denied",
    "nope",
    "rechazar",
    "rechazo",
    "para",
    "detente",
}


def _log(conn, session_id: str, role: str, text: str, run_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO chat_messages(session_id,role,text,run_id,created_at) VALUES(?,?,?,?,?)",
        (session_id, role, text, run_id, now_iso()),
    )


def classify(text: str) -> str:
    """approve | reject | guidance. Keywords first; they are free and certain."""
    cleaned = re.sub(r"[^\w\s]", " ", text.strip().lower()).strip()
    if cleaned in APPROVE_WORDS:
        return "approve"
    if cleaned in REJECT_WORDS:
        return "reject"
    first = cleaned.split()[0] if cleaned else ""
    if first in APPROVE_WORDS and len(cleaned.split()) <= 3:
        return "approve"
    if first in REJECT_WORDS and len(cleaned.split()) <= 3:
        return "reject"
    return "guidance"


class Session:
    """One conversation. Holds no state of its own -- everything is in the DB."""

    def __init__(
        self,
        conn,
        *,
        agent_id: str,
        mandate_jti: str,
        session_id: str | None = None,
        person: str = "buyer",
    ):
        self.conn = conn
        self.agent_id = agent_id
        self.mandate_jti = mandate_jti
        self.person = person
        self.session_id = session_id or new_id("ses")

    # ── state ────────────────────────────────────────────────────────────────
    def active_run(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT run_id FROM agent_runs WHERE session_id=? AND status IN "
            "('running','awaiting_human') ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        return graph._load(self.conn, row["run_id"]) if row else None

    # ── the one entry point ──────────────────────────────────────────────────
    def send(self, text: str) -> list[str]:
        _log(self.conn, self.session_id, "buyer", text)
        run = self.active_run()

        if run and run["status"] == "awaiting_human":
            replies = self._answer_escalation(run, text)
        elif run and run["status"] == "running":
            replies = self._guide(run, text)
        else:
            replies = self._new_request(text)

        for line in replies:
            _log(
                self.conn,
                self.session_id,
                "agent",
                line,
                run_id=(self.active_run() or {}).get("run_id"),
            )
        return replies

    # ── turn shapes ──────────────────────────────────────────────────────────
    def _new_request(self, text: str) -> list[str]:
        run = graph.start(
            self.conn,
            agent_id=self.agent_id,
            mandate_jti=self.mandate_jti,
            request=text,
            session_id=self.session_id,
        )
        run = graph.run_until_pause(self.conn, run["run_id"])
        return self._describe(run)

    def _guide(self, run: dict, text: str) -> list[str]:
        graph.add_guidance(self.conn, run["run_id"], text)
        return ["Noted — replanning with that."] + self._describe(
            graph.run_until_pause(self.conn, run["run_id"])
        )

    def _answer_escalation(self, run: dict, text: str) -> list[str]:
        esc_id = run["escalation_id"]
        if escalation.is_expired(self.conn, esc_id):
            escalation.expire(self.conn, esc_id)
            graph.resume(self.conn, run["run_id"])
            return [
                "That request timed out, so I refused it. Nothing was charged.",
                "Silence never approves a purchase here.",
            ]

        intent = classify(text)
        if intent == "approve":
            result = escalation.resolve(
                self.conn,
                esc_id,
                decision="APPROVE",
                approver=self.person,
                channel="chat",
                sticky=True,
            )
            run = graph.resume(self.conn, run["run_id"])
            outcome = result.get("outcome") or {}
            lines = [
                "Approved. Re-running the check before paying — an approval "
                "authorises a retry, not a bypass."
            ]
            return lines + self._describe(run, outcome)

        if intent == "reject":
            escalation.resolve(
                self.conn, esc_id, decision="REJECT", approver=self.person, channel="chat"
            )
            graph.resume(self.conn, run["run_id"])
            return ["Refused. Nothing was charged."]

        # Guidance while parked: that is a no to this purchase, and a new brief.
        escalation.resolve(
            self.conn, esc_id, decision="REJECT", approver=self.person, channel="chat"
        )
        graph.resume(self.conn, run["run_id"])
        fresh = graph.start(
            self.conn,
            agent_id=self.agent_id,
            mandate_jti=self.mandate_jti,
            request=f"{run['state']['request']} ({text})",
            session_id=self.session_id,
        )
        graph.add_guidance(self.conn, fresh["run_id"], text)
        return ["Understood — dropping that one and looking again."] + self._describe(
            graph.run_until_pause(self.conn, fresh["run_id"])
        )

    # ── rendering ────────────────────────────────────────────────────────────
    def _describe(self, run: dict, outcome: dict | None = None) -> list[str]:
        state = run["state"]
        result = outcome or state.get("result") or {}
        proposal = state.get("proposal") or {}
        lines: list[str] = []

        if proposal.get("concern"):
            lines.append(f"Note: {proposal['concern']}")

        if run["status"] == "awaiting_human":
            diff = result.get("diff") or {}
            lines.append(f"I found: {self._offer_line(state, proposal)}")
            lines.append(f"This needs you: {result.get('detail', 'outside the mandate')}.")
            if diff.get("over_by"):
                lines.append(
                    f"It is {diff['over_by']} over your per-purchase limit of {diff.get('limit')}."
                )
            lines.append(
                "Reply 'approve' to allow it, 'reject' to refuse, or tell me "
                "what to look for instead. No reply within the timeout = refused."
            )
            return lines

        status = result.get("status")
        if status == "captured":
            receipt = result.get("receipt") or {}
            lines.append(
                f"Bought: {receipt.get('title')} for "
                f"{receipt.get('amount')} {receipt.get('currency')}."
            )
            lines.append(
                f"Under mandate {receipt.get('mandate_jti')} · receipt {receipt.get('receipt_id')}."
            )
            if proposal.get("why"):
                lines.append(f"Why this one: {proposal['why']}")
            return lines

        if status in ("rejected", "compensated"):
            lines.append(
                f"Refused: {result.get('reason_code')} — {result.get('detail', '')}".strip(" —")
            )
            lines.append("Nothing was charged.")
            return lines

        if run["status"] == "denied":
            lines.append("I could not find anything I am allowed to buy for that.")
            return lines
        if run["status"] == "failed":
            lines.append("Something broke on my side. Nothing was charged.")
            return lines
        return lines or ["Working on it."]

    def _offer_line(self, state: dict, proposal: dict) -> str:
        for offer in state.get("offers", []):
            if offer["offer_id"] == proposal.get("offer_id"):
                return (
                    f"{offer['title']} at {offer['price']} {offer['currency']} "
                    f"({offer['offer_id']})"
                )
        return proposal.get("offer_id", "an offer")


def transcript(conn, session_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT role,text,created_at FROM chat_messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
    ]
