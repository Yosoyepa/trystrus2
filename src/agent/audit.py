"""The evidence chain (E1-E5), partitioned by mandate.

A single global chain is the obvious design and the wrong one at any scale:
each entry carries a fingerprint of the entry before it, so writing entry N
means first reading entry N-1, and every event in the whole system queues on
one row. Marta's purchase waits behind Juan's for no reason but bookkeeping.

So the chain is partitioned. Each mandate gets its own chain; config edits get
one per agent; everything else lands in `system`. Writers contend only within
a partition, which is where ordering actually matters — the order of Marta's
own events is evidence, the interleaving of hers with a stranger's is not.

One global proof is restored by `checkpoint()`: it hashes the sorted set of
every chain's head into one root and signs that. Tampering with any event in
any chain changes that chain's head, which changes the root, which no longer
matches the signature. E1 and E2 hold exactly as before, per chain; E3 now
covers every chain at once.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .crypto.canonical import canonical_json
from .ids import new_id, now_iso

GENESIS = "0" * 64
SYSTEM_CHAIN = "system"


def chain_key_for(mandate_jti: str | None, agent_id: str | None) -> str:
    """A mandate's events are its own story; an agent's config edits are its own."""
    if mandate_jti:
        return mandate_jti
    if agent_id:
        return f"agent:{agent_id}"
    return SYSTEM_CHAIN


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
    chain_key: str | None = None,
) -> dict[str, Any]:
    from .scrub import scrub_payload

    clean = scrub_payload(payload) if scrub else payload  # E10
    key = chain_key or chain_key_for(mandate_jti, agent_id)
    created_at = now_iso()
    event_id = new_id("evt")
    row = {
        "event_id": event_id,
        "type": type,
        "actor": actor,
        "agent_id": agent_id,
        "run_id": run_id,
        "mandate_jti": mandate_jti,
        "payload": clean,
        "created_at": created_at,
        "chain_key": key,
    }
    conn.execute("BEGIN")
    try:
        # Create the chain if this is its first event, then take a row lock on
        # it. The lock is per chain, so two mandates never wait on each other.
        conn.execute(
            "INSERT INTO chains(chain_key,head_hash,length,updated_at) "
            "VALUES(?,?,0,?) ON CONFLICT (chain_key) DO NOTHING",
            (key, GENESIS, created_at),
        )
        head = conn.execute(
            "SELECT head_hash, length FROM chains WHERE chain_key=? FOR UPDATE", (key,)
        ).fetchone()
        prev_hash = head["head_hash"]
        chain_seq = int(head["length"]) + 1
        digest = _digest(prev_hash, row)
        conn.execute(
            "INSERT INTO audit_events(chain_key,chain_seq,event_id,type,actor,agent_id,"
            "run_id,mandate_jti,payload,prev_hash,hash,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                chain_seq,
                event_id,
                type,
                actor,
                agent_id,
                run_id,
                mandate_jti,
                canonical_json(clean),
                prev_hash,
                digest,
                created_at,
            ),
        )
        conn.execute(
            "UPDATE chains SET head_hash=?, length=?, updated_at=? WHERE chain_key=?",
            (digest, chain_seq, created_at, key),
        )
        if relay:  # E4: business event and audit row commit together
            conn.execute(
                "INSERT INTO outbox(event_id,type,aggregate_id,payload,created_at)"
                " VALUES(?,?,?,?,?)",
                (
                    event_id,
                    type,
                    mandate_jti or run_id or agent_id or "-",
                    canonical_json(clean),
                    created_at,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {
        "event_id": event_id,
        "hash": digest,
        "prev_hash": prev_hash,
        "chain_key": key,
        "chain_seq": chain_seq,
    }


def verify_chain(conn, chain_key: str) -> dict[str, Any]:
    """Recompute one chain from genesis."""
    prev_hash = GENESIS
    checked = 0
    for row in conn.execute(
        "SELECT * FROM audit_events WHERE chain_key=? ORDER BY chain_seq ASC", (chain_key,)
    ):
        rebuilt = {
            "event_id": row["event_id"],
            "type": row["type"],
            "actor": row["actor"],
            "agent_id": row["agent_id"],
            "run_id": row["run_id"],
            "mandate_jti": row["mandate_jti"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
            "chain_key": row["chain_key"],
        }
        if row["prev_hash"] != prev_hash:
            return {
                "valid": False,
                "chain_key": chain_key,
                "checked": checked,
                "seq": row["seq"],
                "chain_seq": row["chain_seq"],
                "error": "prev_hash does not match the previous event",
            }
        expected = _digest(prev_hash, rebuilt)
        if expected != row["hash"]:
            return {
                "valid": False,
                "chain_key": chain_key,
                "checked": checked,
                "seq": row["seq"],
                "chain_seq": row["chain_seq"],
                "error": "row content does not match its hash",
            }
        prev_hash = row["hash"]
        checked += 1
    stored = conn.execute("SELECT head_hash FROM chains WHERE chain_key=?", (chain_key,)).fetchone()
    if stored and stored["head_hash"] != prev_hash:
        return {
            "valid": False,
            "chain_key": chain_key,
            "checked": checked,
            "error": "chain head does not match the replayed events",
        }
    return {"valid": True, "chain_key": chain_key, "checked": checked, "head": prev_hash}


def verify_all(conn) -> dict[str, Any]:
    """What the auditor view calls live. Every chain, then the global root."""
    results = [
        verify_chain(conn, row["chain_key"])
        for row in conn.execute("SELECT chain_key FROM chains ORDER BY chain_key")
    ]
    broken = [r for r in results if not r["valid"]]
    return {
        "valid": not broken,
        "chains": len(results),
        "checked": sum(r["checked"] for r in results),
        "root": compute_root(conn),
        "broken": broken,
    }


def compute_root(conn) -> str:
    """One fingerprint over every chain head. Order is fixed, so it is stable."""
    heads = [
        (r["chain_key"], r["head_hash"])
        for r in conn.execute("SELECT chain_key, head_hash FROM chains ORDER BY chain_key")
    ]
    return hashlib.sha256(
        canonical_json([{"chain_key": k, "head": h} for k, h in heads]).encode("utf-8")
    ).hexdigest()


def checkpoint(conn) -> dict[str, Any]:
    """Sign the root and witness it outside the database (E3).

    Locally the key is a file; in GCP it is KMS EC_SIGN_ED25519 and the root is
    copied to a versioned bucket. A root that only lives in our own database
    proves nothing against us -- the copy outside our perimeter is what turns
    tamper-evidence into accountability.
    """
    from .config import VAR_DIR
    from .crypto.keys import b64u, load_or_create

    rows = conn.execute(
        "SELECT chain_key, head_hash, length FROM chains ORDER BY chain_key"
    ).fetchall()
    heads = {r["chain_key"]: r["head_hash"] for r in rows}
    events = sum(int(r["length"]) for r in rows)
    root = compute_root(conn)
    payload = {"root": root, "chains": len(rows), "events": events, "at": now_iso()}
    signature = b64u(load_or_create("evidence_root").sign(canonical_json(payload).encode("utf-8")))
    conn.execute(
        "INSERT INTO checkpoints(root_hash,chain_heads,signature,chains_covered,"
        "events_covered,created_at) VALUES(?,?,?,?,?,?)",
        (root, canonical_json(heads), signature, len(rows), events, payload["at"]),
    )
    witness = VAR_DIR / "witness"
    witness.mkdir(exist_ok=True)
    (witness / f"root-{payload['at'].replace(':', '')}.json").write_text(
        json.dumps({**payload, "signature": signature, "chain_heads": heads}, indent=2),
        encoding="utf-8",
    )
    return {**payload, "signature": signature}


def verify_checkpoint(conn) -> dict[str, Any]:
    """Does the latest signed root still describe the events we have?

    Two questions, and both must pass. Comparing roots alone is not enough:
    `chains.head_hash` is a stored summary, so editing an event's payload leaves
    the root untouched while the chain no longer replays. So the events are
    replayed first, and only then is the root compared. A checkpoint over a
    summary nobody re-derives is a signature on a promise.
    """
    row = conn.execute("SELECT * FROM checkpoints ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return {"valid": None, "detail": "no checkpoint has been signed yet"}
    replay = verify_all(conn)
    current = compute_root(conn)
    if not replay["valid"]:
        broken = replay["broken"][0]
        return {
            "valid": False,
            "signed_root": row["root_hash"],
            "current_root": current,
            "at": row["created_at"],
            "detail": f"chain {broken['chain_key']} no longer replays: {broken['error']}",
        }
    if row["root_hash"] != current:
        return {
            "valid": False,
            "signed_root": row["root_hash"],
            "current_root": current,
            "at": row["created_at"],
            "detail": "chains changed since this checkpoint (new events, or a rewritten head)",
        }
    return {
        "valid": True,
        "signed_root": row["root_hash"],
        "current_root": current,
        "at": row["created_at"],
        "detail": "",
    }


# Kept so existing callers keep working; the chain is plural now.
def sign_root(conn) -> dict[str, Any]:
    return checkpoint(conn)
