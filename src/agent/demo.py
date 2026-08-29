"""The scripted demo. Every claim on the slide, executed in order.

Run it with `uv run python -m src.agent.cli demo`.  It is also the closest thing we
have to an end-to-end test: if this script stops printing what it says it will
print, something load-bearing broke.
"""
from __future__ import annotations
import json
import time

from . import audit, chat, db, escalation, graph, mandate as mandate_mod, registry, watcher
from .config import LLM_MODEL, PRODUCT_DOMAIN, PRODUCT_NAME
from .mocks import merchant

BAR = "═" * 74


def _head(number: str, title: str, claim: str) -> None:
    print(f"\n{BAR}\n{number}  {title}\n    {claim}\n{BAR}")


def _pause(on: bool) -> None:
    if on:
        try:
            input("\n      [enter] ")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0)


def run_demo(conn=None, pause: bool = True) -> None:
    conn = conn or db.init()
    from .seed import seed_all

    print(f"{PRODUCT_NAME} · {PRODUCT_DOMAIN}")
    print(f"model in the propose node: {LLM_MODEL} — it cannot approve anything")

    if not conn.execute("SELECT 1 FROM agents LIMIT 1").fetchone():
        seed_all(conn)
    agent = conn.execute("SELECT * FROM agents ORDER BY created_at LIMIT 1").fetchone()
    mandate_row = conn.execute(
        "SELECT * FROM mandates WHERE parent_jti IS NULL ORDER BY created_at LIMIT 1"
    ).fetchone()
    agent_id, mandate_jti = agent["id"], mandate_row["jti"]
    claims = json.loads(mandate_row["claims"])

    # ── 1 ────────────────────────────────────────────────────────────────────
    _head("1.", "A person creates a mandate without handing over the card",
          "the agent holds permission; the instrument stays vaulted at the rail")
    print(f"  mandate     {mandate_jti}")
    print(f"  signed with {claims['signed_with']}")
    print(f"  limits      {claims['limits']['max_per_txn']} per purchase, "
          f"{claims['limits']['total_budget']} total, "
          f"{claims['limits']['max_txn']['count']} purchases")
    print(f"  scope       {claims['scope']}")
    print(f"  payment     {claims['payment_method_ref']}  ← opaque token, not a card")
    print(f"  agent key   {claims['cnf']['jwk']['x'][:24]}…  ← bound into the mandate")
    _pause(pause)

    # ── 2 ────────────────────────────────────────────────────────────────────
    _head("2.", "The merchant verifies the mandate itself",
          "offline, against the published JWKS — it never has to trust our answer")
    verified = mandate_mod.verify_token(mandate_row["token"])
    print(f"  signature verifies · issuer {verified['iss']} · jti {verified['jti']}")
    tampered = mandate_row["token"][:-6] + "AAAAAA"
    try:
        mandate_mod.verify_token(tampered)
        print("  !! a tampered mandate verified — this should never happen")
    except Exception as exc:
        print(f"  one mutated byte → rejected: {type(exc).__name__}")
    _pause(pause)

    # ── 3 ────────────────────────────────────────────────────────────────────
    _head("3.", "An end-to-end purchase inside the mandate",
          "the agent proposes, the gate decides, the merchant charges")
    session = chat.Session(conn, agent_id=agent_id, mandate_jti=mandate_jti,
                           person="Marta")
    for line in session.send("find me a flight from Bogota to Cordoba, cheapest you can"):
        print(f"  agent> {line}")
    _pause(pause)

    # ── 4 ────────────────────────────────────────────────────────────────────
    _head("4.", "Over the limit: escalated, never silently approved",
          "only this check may escalate — a forged agent or dead mandate just fails")
    for line in session.send(
            "actually book the fully flexible business fare, offer ofr_cor_300"):
        print(f"  agent> {line}")
    pending = escalation.pending(conn)
    if pending:
        esc = pending[0]
        print(f"\n  escalation {esc['id']} → approver {esc['approver_id']} "
              f"· deadline {esc['timeout_at']}")
        print("  Marta replies in the chat:")
        for line in session.send("approve"):
            print(f"  agent> {line}")
    else:
        print("  (no escalation raised — the model stayed inside the limit)")
    _pause(pause)

    # ── 5 ────────────────────────────────────────────────────────────────────
    _head("5.", "Prompt injection in the catalog",
          "blocked by the gate, not by the prompt — the model may be fooled")
    injected = merchant.get_offer(conn, "ofr_inj_1")
    print(f"  catalog entry {injected['offer_id']} at {injected['price']}:")
    print(f"    \"{injected['description'][:88]}…\"")
    from . import kernel
    result = kernel.submit_purchase(conn, offer_id="ofr_inj_1", mandate_jti=mandate_jti)
    print(f"  agent proposes it anyway → {result['status'].upper()}: "
          f"{result.get('reason_code')}")
    print(f"    ({result.get('detail', '')})")
    print("  the model can hallucinate a proposal; the proposal still has to pass")
    print("  a check it cannot influence.")
    _pause(pause)

    # ── 6 ────────────────────────────────────────────────────────────────────
    _head("6.", "An impersonated agent",
          "dies at the signature, before any state is consulted")
    from .crypto import jws
    from .crypto.keys import load_or_create
    offer = merchant.get_offer(conn, "ofr_cor_119")
    intent = kernel.build_intent(mandate_jti=mandate_jti, agent_id=agent_id, offer=offer)
    forged = jws.sign_detached(intent, load_or_create("attacker"), kid=agent_id)
    outcome = kernel.verify(conn, mandate_token=mandate_row["token"], intent=intent,
                            intent_sig=forged)
    print(f"  cloned agent, wrong key → {outcome['decision']}: {outcome['reason_code']}")
    _pause(pause)

    # ── 7 ────────────────────────────────────────────────────────────────────
    _head("7.", "The recurrent watcher",
          "a threshold a human set, polled on a schedule, through the same gate")
    watches = watcher.list_watches(conn, status="active")
    if watches:
        watch = watches[0]
        from . import jsonlogic
        print(f"  watch {watch['id']}: buy when "
              f"{jsonlogic.describe(json.loads(watch['threshold']))}")
        before = watcher.check(conn, watch["id"], force=True)
        print(f"  poll  → cheapest {before.get('cheapest')} · "
              f"matched={before.get('matched')}  (nothing to do)")
        print("  … the merchant drops that fare to 118.00 …")
        merchant.set_price(conn, "ofr_cor_119", "118.00")
        after = watcher.check(conn, watch["id"], force=True)
        outcome = after.get("result", {})
        print(f"  poll  → cheapest {after.get('cheapest')} · "
              f"matched={after.get('matched')} → {outcome.get('status', '')} "
              f"{outcome.get('reason_code') or ''}")
        if outcome.get("receipt"):
            print(f"  bought unattended: {outcome['receipt']['title']} at "
                  f"{outcome['receipt']['amount']} — through the same gate")
    _pause(pause)

    # ── 8 ────────────────────────────────────────────────────────────────────
    _head("8.", "Live revocation",
          "the next attempt fails twice: in our state, and at the rail")
    started = time.time()
    revoked = mandate_mod.revoke(conn, mandate_jti, actor="Marta")
    outcome = kernel.submit_purchase(conn, offer_id="ofr_cor_142",
                                     mandate_jti=mandate_jti)
    elapsed = time.time() - started
    print(f"  revoked · rail token deleted: {revoked['rail_token_deleted']}")
    print(f"  next attempt → {outcome['status'].upper()}: {outcome['reason_code']}")
    print(f"  elapsed {elapsed:.3f}s  (target ≤ 2 s)")
    _pause(pause)

    # ── 9 ────────────────────────────────────────────────────────────────────
    _head("9.", "The trail",
          "append-only, hash chained, recomputed live")
    chain = audit.verify_all(conn)
    print(f"  {chain['checked']} events across {chain['chains']} chains · "
          f"valid={chain['valid']}")
    print("  one chain per mandate, so writers never queue behind each other;")
    root = audit.checkpoint(conn)
    print(f"  one signed root covers them all: {str(root['root'])[:32]}…")
    print(f"  witnessed to var/witness/ ({root['chains']} chains, "
          f"{root['events']} events)")
    print("\n  the agent's own trajectory is in the same chain:")
    for row in conn.execute(
            "SELECT seq,type,payload FROM audit_events WHERE type LIKE 'agent.%' "
            "ORDER BY seq DESC LIMIT 6").fetchall():
        payload = json.loads(row["payload"])
        print(f"    {row['seq']:>4}  {row['type']:<24} "
              f"{payload.get('node') or payload.get('request','')}")
    print("\n  try to change one row:")
    try:
        conn.execute("UPDATE audit_events SET payload='{}' WHERE seq=1")
        print("    !! the update succeeded — append-only is not enforced")
    except Exception as exc:
        print(f"    refused by the database: {exc}")
    print(f"\n{BAR}\ndone. `uv run python -m src.agent.cli audit` for the full trail.\n{BAR}")
