"""Who is allowed to change an agent.

Until now the console recorded *who claimed* to make a change. That is not the
same as knowing who made it, and an audit trail of unverified claims is a
weaker thing than it looks: the attribution is only as good as the honesty of
whoever typed the name.

So a mutation now needs a token, and the audit event records an authenticated
principal rather than a string someone supplied. This is deliberately small —
bearer tokens hashed at rest, four roles, one permission table. It is not SSO,
and the buyer's own authority still comes from the passkey over the mandate,
not from anything here. This governs the console, which is a different question
from whether money may move: no token in this file can widen a spending limit
(K1). The worst an admin can do is make the agent propose stupid things.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

from . import audit

# role -> what it may do
PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "agent.create",
        "agent.publish",
        "agent.people",
        "agent.status",
        "watch.create",
        "watch.cancel",
        "escalation.resolve",
        "mandate.revoke",
        "read",
    },
    # An owner may resolve escalations, but only for agents where they are the
    # named approver -- the role grants the capability, the agent attachment
    # grants the instance. Both checks run; neither is sufficient alone.
    "owner": {
        "agent.create",
        "agent.publish",
        "agent.people",
        "agent.status",
        "watch.create",
        "watch.cancel",
        "mandate.revoke",
        "escalation.resolve",
        "read",
    },
    "approver": {"escalation.resolve", "read"},
    "auditor": {"read"},
    "member": {"read"},
}

# Actions that only make sense against an agent you are attached to.
AGENT_SCOPED = {
    "agent.publish",
    "agent.people",
    "agent.status",
    "watch.create",
    "watch.cancel",
    "escalation.resolve",
}


class AuthError(Exception):
    """Authentication or authorisation failed. Fails closed (S3)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    person_id: str
    name: str
    role: str

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


def hash_token(token: str) -> str:
    """Tokens are stored hashed. A leaked database is not a set of credentials."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(conn, person_id: str) -> str:
    """Mint a token and store only its hash. Shown once, never recoverable."""
    token = f"tt_{secrets.token_urlsafe(24)}"
    updated = conn.execute(
        "UPDATE people SET token_hash=? WHERE id=?", (hash_token(token), person_id)
    ).rowcount
    if not updated:
        raise AuthError("NO_SUCH_PERSON", person_id)
    audit.append(conn, "person.token.issued", {"person_id": person_id}, actor=person_id)
    return token


def authenticate(conn, token: str | None) -> Principal:
    token = token or os.environ.get("TT_TOKEN", "")
    if not token:
        raise AuthError("NO_TOKEN", "set TT_TOKEN, or pass --token; the console is not anonymous")
    row = conn.execute(
        "SELECT id, name, role FROM people WHERE token_hash=?", (hash_token(token),)
    ).fetchone()
    if row is None:
        # Constant message on purpose: a distinct "unknown token" reply tells an
        # attacker which of their guesses exist.
        raise AuthError("BAD_TOKEN", "not a valid token")
    return Principal(row["id"], row["name"], row["role"])


def authorize(conn, principal: Principal, action: str, agent_id: str | None = None) -> None:
    allowed = PERMISSIONS.get(principal.role, set())
    if action not in allowed:
        raise AuthError("FORBIDDEN", f"{principal} may not {action}")
    if action in AGENT_SCOPED and agent_id and principal.role != "admin":
        row = conn.execute(
            "SELECT owner_id, approver_id, auditor_id FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        if row is None:
            raise AuthError("NO_SUCH_AGENT", agent_id)
        attached = {row["owner_id"], row["approver_id"], row["auditor_id"]}
        if principal.person_id not in attached:
            raise AuthError("FORBIDDEN", f"{principal} is not attached to {agent_id}")
        if action == "escalation.resolve" and principal.person_id != row["approver_id"]:
            raise AuthError("FORBIDDEN", f"{principal} is not the approver for {agent_id}")
        if action != "escalation.resolve" and principal.person_id != row["owner_id"]:
            raise AuthError("FORBIDDEN", f"{principal} does not own {agent_id}")


def require(conn, token: str | None, action: str, agent_id: str | None = None) -> Principal:
    """Authenticate then authorise. Every console mutation goes through here."""
    principal = authenticate(conn, token)
    try:
        authorize(conn, principal, action, agent_id)
    except AuthError as exc:
        audit.append(
            conn,
            "auth.denied",
            {
                "person_id": principal.person_id,
                "action": action,
                "agent_id": agent_id,
                "reason": exc.code,
            },
            actor=principal.person_id,
            agent_id=agent_id,
        )
        raise
    return principal
