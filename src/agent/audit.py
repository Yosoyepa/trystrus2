"""The evidence chain (E1-E7).

Every append writes the audit row and its outbox event in ONE transaction (E4).
The hash covers the previous hash plus the canonical row, so mutating,
deleting or reordering any event breaks verification from that point on (E2).
"""
from __future__ import annotations
import hashlib
import json
from typing import Any

from .crypto.canonical import canonical_json
from .ids import new_id, now_iso

GENESIS = "0" * 64


def _digest(prev_hash: str, row: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(row)).encode("utf-8")).hexdigest()


def append(
    conn,
    type: str,
    payload: dict[str, Any],
    *,
    actor: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    mandate_jti: str | None = None,
    relay: bool = True,
    scrub: bool = True,
) -> dict[str, Any]:
    from .scrub import scrub_payload  # local import: keeps the chain dependency-light

    clean = scrub_payload(payload) if scrub else payload  # E10
    created_at = now_iso()
    event_id = new_id("evt")
    row = {
        "event_id": event_id, "type": type, "actor": actor, "agent_id": agent_id,
        "run_id": run_id, "mandate_jti": mandate_jti, "payload": clean,
        "created_at": created_at,
    }
    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute(
            "SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = last["hash"] if last else GENESIS
        digest = _digest(prev_hash, row)
        conn.execute(
            "INSERT INTO audit_events(event_id,type,actor,agent_id,run_id,mandate_jti,"
            "payload,prev_hash,hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event_id, type, actor, agent_id, run_id, mandate_jti,
             canonical_json(clean), prev_hash, digest, created_at),
        )
        if relay:  # E4: business event and audit row commit together
            conn.execute(
                "INSERT INTO outbox(event_id,type,aggregate_id,payload,created_at)"
                " VALUES(?,?,?,?,?)",
                (event_id, type, mandate_jti or run_id or agent_id or "-",
                 canonical_json(clean), created_at),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"event_id": event_id, "hash": digest, "prev_hash": prev_hash}


def verify_chain(conn) -> dict[str, Any]:
    """Recompute the whole chain. This is what the auditor view calls live."""
    prev_hash = GENESIS
    checked = 0
    for row in conn.execute("SELECT * FROM audit_events ORDER BY seq ASC"):
        rebuilt = {
            "event_id": row["event_id"], "type": row["type"], "actor": row["actor"],
            "agent_id": row["agent_id"], "run_id": row["run_id"],
            "mandate_jti": row["mandate_jti"], "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        if row["prev_hash"] != prev_hash:
            return {"valid": False, "checked": checked, "seq": row["seq"],
                    "error": "prev_hash does not match the previous event"}
        expected = _digest(prev_hash, rebuilt)
        if expected != row["hash"]:
            return {"valid": False, "checked": checked, "seq": row["seq"],
                    "error": "row content does not match its hash"}
        prev_hash = row["hash"]
        checked += 1
    return {"valid": True, "checked": checked, "head": prev_hash}


def sign_root(conn) -> dict[str, Any]:
    """Sign the head of the chain (E3).

    Locally this is an Ed25519 key on disk; in GCP it is KMS EC_SIGN_ED25519 and
    the root is copied to a versioned bucket outside our perimeter -- a root that
    only lives in our own database proves nothing against us.
    """
    from .crypto.keys import b64u, load_or_create

    head = conn.execute(
        "SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if not head:
        return {"root": GENESIS, "seq": 0, "signature": None}
    key = load_or_create("evidence_root")
    payload = {"root": head["hash"], "seq": head["seq"], "at": now_iso()}
    signature = b64u(key.sign(canonical_json(payload).encode("utf-8")))
    (conn, )  # roots are witnessed externally in deployment; local copy below
    from .config import VAR_DIR
    witness = VAR_DIR / "witness"
    witness.mkdir(exist_ok=True)
    (witness / f"root-{head['seq']}.json").write_text(
        json.dumps({**payload, "signature": signature}, indent=2), encoding="utf-8"
    )
    return {**payload, "signature": signature}
