"""TryTrust command line. `uv run python -m src.agent.cli <command>`"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import (
    audit,
    auth,
    chat,
    db,
    escalation,
    graph,
    jsonlogic,
    kernel,
    limits,
    memory,
    registry,
    relay,
    watcher,
)
from . import mandate as mandate_mod
from .config import LLM_MODEL, PRODUCT_DOMAIN, PRODUCT_NAME
from .mocks import merchant

BAR = "─" * 72


def _conn():
    return db.init()


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


# ── setup ────────────────────────────────────────────────────────────────────
def cmd_reset(args) -> None:
    conn = db.connect()
    db.drop_all(conn)
    db.init()
    print(f"database cleared and re-created: {db.DSN.rsplit('@', 1)[-1]}")


def cmd_seed(args) -> None:
    from .seed import seed_all

    result = seed_all(_conn())
    print(f"{PRODUCT_NAME} · {PRODUCT_DOMAIN}")
    print(BAR)
    _print(result)
    print("\nnext:  uv run python -m src.agent.cli chat")


def cmd_demo(args) -> None:
    """The scripted run, as a judge would drive it."""
    from .demo import run_demo

    run_demo(_conn(), pause=not args.fast)


# ── chat ─────────────────────────────────────────────────────────────────────
def _default_session(conn):
    agent = conn.execute("SELECT id FROM agents ORDER BY created_at LIMIT 1").fetchone()
    mandate = conn.execute(
        "SELECT jti FROM mandates WHERE status='active' AND parent_jti IS NULL "
        "ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not agent or not mandate:
        sys.exit("nothing seeded yet — run: uv run python -m src.agent.cli seed")
    return agent["id"], mandate["jti"]


def cmd_chat(args) -> None:
    conn = _conn()
    agent_id, mandate_jti = _default_session(conn)
    session = chat.Session(conn, agent_id=agent_id, mandate_jti=mandate_jti, person=args.person)
    print(f"{PRODUCT_NAME} · agent {agent_id} · mandate {mandate_jti}")
    print(f"model: {LLM_MODEL} (proposes only — it cannot approve anything)")
    print(
        "type your request. 'approve' / 'reject' answer an escalation. "
        "':q' quits, ':audit' shows the trail."
    )
    print(BAR)
    while True:
        try:
            text = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text in (":q", ":quit", "exit"):
            return
        if text == ":audit":
            cmd_audit(argparse.Namespace(limit=15, mandate=None))
            continue
        if text == ":verify":
            _print(audit.verify_all(conn))
            continue
        for line in session.send(text):
            print(f"agent> {line}")


def cmd_telegram(args) -> None:
    """Long-poll Telegram so the same chat as /agent/dispatch runs on a phone."""
    import asyncio

    from . import service
    from . import telegram as tg

    if not tg.bot_token():
        sys.exit("set TELEGRAM_BOT_TOKEN (BotFather) first")
    service.bootstrap()
    conn = _conn()
    stop = asyncio.Event()
    print(f"{PRODUCT_NAME} · telegram polling · Ctrl-C to stop")
    print(BAR)
    try:
        asyncio.run(tg.poll_forever(stop, conn_factory=lambda: conn))
    except KeyboardInterrupt:
        print()


def cmd_ask(args) -> None:
    conn = _conn()
    agent_id, mandate_jti = _default_session(conn)
    session = chat.Session(
        conn,
        agent_id=agent_id,
        mandate_jti=mandate_jti,
        person=args.person,
        session_id=args.session,
    )
    for line in session.send(" ".join(args.text)):
        print(f"agent> {line}")
    print(f"\n(session {session.session_id})")


# ── configuration platform ───────────────────────────────────────────────────
def cmd_agents(args) -> None:
    conn = _conn()
    rows = registry.list_agents(conn)
    print(f"{'id':<24}{'name':<18}{'owner':<10}{'approver':<10}{'ver':<5}status")
    print(BAR)
    for r in rows:
        print(
            f"{r['id']:<24}{r['name']:<18}{str(r['owner_name']):<10}"
            f"{str(r['approver_name']):<10}v{r['current_version']:<4}{r['status']}"
        )


def cmd_agent_show(args) -> None:
    conn = _conn()
    agent = registry.get_agent(conn, args.agent_id)
    version = registry.get_version(conn, args.agent_id)
    _print(
        {
            "agent": dict(agent),
            "current_ontology": json.loads(version["ontology"]),
            "history": [
                {
                    "version": h["version"],
                    "by": h["changed_by_name"],
                    "reason": h["reason"],
                    "at": h["created_at"],
                }
                for h in registry.history(conn, args.agent_id)
            ],
        }
    )


def cmd_agent_publish(args) -> None:
    conn = _conn()
    from .ontology import load_file

    who = auth.require(conn, args.token, "agent.publish", args.agent_id)
    version = registry.publish_version(
        conn, args.agent_id, load_file(args.file), changed_by=who.person_id, reason=args.reason
    )
    print(f"published v{version} for {args.agent_id}")
    print("running runs keep the version they started with (K3).")


def cmd_token(args) -> None:
    """Mint a console credential. Shown once; only its hash is stored."""
    conn = _conn()
    token = auth.issue_token(conn, args.person_id)
    print(f"  {token}\n\n  Shown once. Use it with:  export TT_TOKEN={token}")


def cmd_people(args) -> None:
    conn = _conn()
    for r in registry.list_people(conn):
        print(f"{r['id']:<24}{r['name']:<12}{r['role']:<10}{r['email'] or ''}")


def cmd_assign(args) -> None:
    conn = _conn()
    who = auth.require(conn, args.token, "agent.people", args.agent_id)
    registry.set_people(
        conn,
        args.agent_id,
        owner_id=args.owner,
        approver_id=args.approver,
        auditor_id=args.auditor,
        actor=who.person_id,
    )
    print("updated; the change is in the audit trail (E12).")


# ── watches / cron ───────────────────────────────────────────────────────────
def cmd_watches(args) -> None:
    conn = _conn()
    rows = watcher.list_watches(conn)
    print(f"{'id':<24}{'status':<11}{'every':<8}{'last':<10}threshold")
    print(BAR)
    for r in rows:
        print(
            f"{r['id']:<24}{r['status']:<11}{str(r['interval_s']) + 's':<8}"
            f"{str(r['last_seen_price']):<10}"
            f"{jsonlogic.describe(json.loads(r['threshold']))}"
        )


def cmd_watch_add(args) -> None:
    conn = _conn()
    agent_id, mandate_jti = _default_session(conn)
    who = auth.require(conn, args.token, "watch.create", agent_id)
    args.by = who.person_id
    result = watcher.create_watch(
        conn,
        agent_id=agent_id,
        mandate_jti=mandate_jti,
        query={"destination": args.destination, "category": args.category},
        threshold={"<=": [{"var": "offer.price"}, float(args.under)]},
        interval_s=args.every,
        autobuy=not args.notify_only,
        created_by=args.by,
    )
    _print(result)


def cmd_tick(args) -> None:
    _print(watcher.tick(_conn()))


def cmd_watch_daemon(args) -> None:
    print(f"watching every {args.every}s — Ctrl-C to stop")
    watcher.run_forever(_conn(), every_s=args.every, max_ticks=args.ticks)


def cmd_cron(args) -> None:
    from .config import REPO_ROOT

    print(watcher.CRONTAB_HINT.format(repo=REPO_ROOT))


# ── merchant / mandate operations ────────────────────────────────────────────
def cmd_offers(args) -> None:
    conn = _conn()
    for offer in merchant.search_offers(conn, limit=50):
        flag = " ⚠ injection" if offer["offer_id"].startswith("ofr_inj") else ""
        print(
            f"{offer['offer_id']:<16}{offer['price']:>8} {offer['currency']}  "
            f"{offer['title']}{flag}"
        )


def cmd_price(args) -> None:
    _print(merchant.set_price(_conn(), args.offer_id, args.amount))


def cmd_revoke(args) -> None:
    conn = _conn()
    jti = args.jti or _default_session(conn)[1]
    who = auth.require(conn, args.token, "mandate.revoke")
    _print(mandate_mod.revoke(conn, jti, actor=who.person_id))


def cmd_mandate(args) -> None:
    conn = _conn()
    jti = args.jti or _default_session(conn)[1]
    row = mandate_mod.get(conn, jti)
    _print(
        {
            "jti": row["jti"],
            "status": row["status"],
            "spent": row["spent_total"],
            "reserved": row["reserved_amount"],
            "txn_count": row["txn_count"],
            "claims": json.loads(row["claims"]),
            "memory": memory.summarise(conn, jti),
        }
    )


def cmd_jwks(args) -> None:
    _print(mandate_mod.jwks())


# ── the control tower ────────────────────────────────────────────────────────
def cmd_audit(args) -> None:
    conn = _conn()
    sql = "SELECT * FROM audit_events"
    params: tuple = ()
    if args.mandate:
        sql += " WHERE mandate_jti=?"
        params = (args.mandate,)
    sql += " ORDER BY seq DESC LIMIT ?"
    params += (args.limit,)
    rows = list(reversed(conn.execute(sql, params).fetchall()))
    print(f"{'seq':<5}{'type':<28}{'actor/agent':<22}payload")
    print(BAR)
    for r in rows:
        payload = json.loads(r["payload"])
        summary = ", ".join(
            f"{k}={v}" for k, v in list(payload.items())[:3] if not isinstance(v, (dict, list))
        )
        print(
            f"{r['seq']:<5}{r['type']:<28}"
            f"{str(r['actor'] or r['agent_id'] or '-')[:20]:<22}{summary[:70]}"
        )


def cmd_verify(args) -> None:
    conn = _conn()
    result = audit.verify_all(conn)
    print(f"chains: {result['chains']}   events: {result['checked']}   valid: {result['valid']}")
    print(f"root:   {result['root']}")
    if result["broken"]:
        print(BAR)
        for b in result["broken"]:
            print(f"  BROKEN {b['chain_key']} at chain_seq {b.get('chain_seq')}: {b['error']}")
        return
    if args.per_chain:
        print(BAR)
        for row in conn.execute("SELECT chain_key, length FROM chains ORDER BY chain_key"):
            print(f"  {row['chain_key']:<34}{row['length']:>5} events")
    print(BAR)
    _print(audit.checkpoint(conn))


def cmd_runs(args) -> None:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?", (args.limit,)
    ).fetchall()
    print(f"{'run_id':<24}{'ver':<5}{'node':<14}{'status':<16}request")
    print(BAR)
    for r in rows:
        state = json.loads(r["state"])
        print(
            f"{r['run_id']:<24}v{r['agent_version']:<4}{r['node']:<14}"
            f"{r['status']:<16}{state.get('request', '')[:34]}"
        )


def cmd_escalations(args) -> None:
    _print(escalation.pending(_conn()))


def cmd_resolve(args) -> None:
    conn = _conn()
    esc_row = escalation.get(conn, args.escalation_id)
    agent_row = conn.execute(
        "SELECT agent_id FROM mandates WHERE jti=?", (esc_row["mandate_jti"],)
    ).fetchone()
    who = auth.require(
        conn, args.token, "escalation.resolve", agent_row["agent_id"] if agent_row else None
    )
    result = escalation.resolve(
        conn,
        args.escalation_id,
        decision=args.decision.upper(),
        approver=who.person_id,
        channel="cli",
        sticky=args.sticky,
    )
    esc = escalation.get(conn, args.escalation_id)
    if esc["run_id"]:
        graph.resume(conn, esc["run_id"])
    _print(result)


def cmd_relay(args) -> None:
    conn = _conn()
    relay.default_subscribers()
    print("subscribers:", ", ".join(relay.SUBSCRIBERS) or "(none)")
    _print(relay.pending(conn))
    _print(relay.drain(conn, batch=args.batch))


def cmd_relay_daemon(args) -> None:
    conn = _conn()
    relay.default_subscribers()
    print(f"relaying every {args.every}s — Ctrl-C to stop. Run several; SKIP LOCKED shards them.")
    relay.run_forever(conn, every_s=args.every, max_passes=args.passes)


def cmd_limits(args) -> None:
    conn = _conn()
    snap = limits.snapshot(conn)
    print("QUOTA (override with TT_* environment variables)")
    print(BAR)
    for key, value in snap["quota"].items():
        print(f"  {key:<28}{value}")
    if snap["buckets"]:
        print("\nRATE BUCKETS (tokens remaining)")
        print(BAR)
        for b in snap["buckets"]:
            print(f"  {b['key']:<34}{b['tokens']:<8}{b['updated_at']}")
    if snap["counters"]:
        print("\nCOUNTERS")
        print(BAR)
        for c in snap["counters"]:
            print(f"  {c['key']:<34}{c['window']:<16}{c['value']}")
    if snap["locks"]:
        print("\nLOCKS HELD")
        print(BAR)
        for lk in snap["locks"]:
            print(f"  {lk['name']:<20}{lk['holder']:<26}expires {lk['expires_at']}")


def cmd_mcp_check(args) -> None:
    """Does a merchant's MCP server honour the frozen contract?"""
    from .ports.mcp_client import McpMerchant

    info = McpMerchant(args.url).inspect()
    print(f"server   {info['url']}")
    print(BAR)
    for tool in info["tools"]:
        print(f"  {tool['name']:<20}{tool['description'][:46]}")
    print(BAR)
    print(f"  missing from the contract : {info['missing'] or 'none'}")
    print(f"  offered but not used      : {info['unexpected'] or 'none'}")
    print(f"  names that suggest money  : {info['suspicious'] or 'none'}")
    print(f"\n  contract holds: {info['contract_ok']}")
    if not info["contract_ok"]:
        raise SystemExit(1)


def cmd_mcp_demo(args) -> None:
    """Buy from a real merchant MCP, through the gate."""
    import json as _json

    from .ports.base import TOOLS, search_all
    from .ports.setup import setup

    conn = _conn()
    url = args.url or "http://localhost:3000/api/mcp"
    which = "mami_url" if args.merchant == "mami" else "vuelaya_url"
    report = setup(**{which: url})
    merchant_id = "mami" if args.merchant == "mami" else "vuelaya-mcp"
    if "unreachable" in report.get(merchant_id, {}):
        raise SystemExit(f"  {merchant_id}: {report[merchant_id]['unreachable']}")

    print(f"merchant {merchant_id} at {url}")
    print(f"  callable : {TOOLS.callable_names(merchant_id)}")
    print(
        f"  refused  : {[r['name'] for r in TOOLS.refused if r['merchant_id'] == merchant_id]}"
        "   <- settles with no mandate; the kernel settles instead"
    )

    row = conn.execute(
        "SELECT claims FROM mandates WHERE claims::jsonb->>'currency'=? "
        "AND status='active' LIMIT 1",
        (args.currency,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"  no active {args.currency} mandate; run seed first")
    claims = _json.loads(row["claims"])
    print(
        f"\nmandate {claims['jti']}  {claims['currency']}  "
        f"max/txn {claims['limits']['max_per_txn']}"
    )

    print("\n1. search")
    offers = [
        o
        for o in search_all(
            conn,
            allowed=claims["scope"]["merchants"],
            destination=args.destination,
            query=args.query,
        )
        if o["merchant_id"] == merchant_id
    ]
    for o in offers[:4]:
        print(f"   {o['price']:>12} {o['currency']}  {o['title'][:52]}")
    if not offers:
        raise SystemExit("   the merchant returned nothing this mandate may buy")

    print("\n2. buy the cheapest, through the gate")
    pick = min(offers, key=lambda o: float(o["price"]))
    result = kernel.submit_purchase(
        conn, offer_id=pick["offer_id"], mandate_jti=claims["jti"], merchant_id=merchant_id
    )
    print(
        f"   {result['status'].upper()} {result.get('reason_code') or ''} "
        f"{result.get('detail', '')}"
    )
    if result.get("receipt"):
        r = result["receipt"]
        print(
            f"   {r['receipt_id']}  {r['amount']} {r['currency']}  under mandate {r['mandate_jti']}"
        )

    if args.revoke:
        print("\n3. revoke, then try the same purchase again")
        mandate_mod.revoke(conn, claims["jti"], actor="cli")
        after = kernel.submit_purchase(
            conn, offer_id=pick["offer_id"], mandate_jti=claims["jti"], merchant_id=merchant_id
        )
        print(f"   {after['status'].upper()}: {after.get('reason_code')}")


def cmd_merchants(args) -> None:
    from .ports.base import MERCHANTS, TOOLS
    from .ports.setup import setup

    report = setup(vuelaya_url=args.vuelaya, mami_url=args.mami)
    print(f"{'merchant':<16}{'currency':<10}{'callable tools':<44}refused")
    print(BAR)
    for merchant_id, m in MERCHANTS.items():
        refused = [r["name"] for r in TOOLS.refused if r["merchant_id"] == merchant_id]
        tools = ", ".join(TOOLS.callable_names(merchant_id))[:42]
        print(f"{merchant_id:<16}{m.currency:<10}{tools:<44}{refused or ''}")
    for merchant_id, info in report.items():
        if "unreachable" in info:
            print(f"{merchant_id:<16}{'-':<10}unreachable: {info['unreachable'][:44]}")


def cmd_protocols(args) -> None:
    from .protocols.review import summary

    print(summary())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="trytrust", description=f"{PRODUCT_NAME} — {PRODUCT_DOMAIN}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=fn)
        return p

    add("reset", cmd_reset, "delete the local database")
    add("seed", cmd_seed, "create demo people, agent, mandate, catalog and a watch")
    p = add("demo", cmd_demo, "run the full scripted demo")
    p.add_argument("--fast", action="store_true", help="no pauses")

    p = add("chat", cmd_chat, "interactive chat with the agent")
    p.add_argument("--person", default="Marta")
    add("telegram", cmd_telegram, "talk to the agent over Telegram (long-poll)")
    p = add("ask", cmd_ask, "one-shot request")
    p.add_argument("text", nargs="+")
    p.add_argument("--person", default="Marta")
    p.add_argument("--session", default=None)

    add("agents", cmd_agents, "list agents")
    p = add("agent", cmd_agent_show, "show one agent, its ontology and history")
    p.add_argument("agent_id")
    p = add("publish", cmd_agent_publish, "publish a new ontology version")
    p.add_argument("agent_id")
    p.add_argument("file")
    p.add_argument("--by", default=None)
    p.add_argument("--reason", default="")
    add("people", cmd_people, "list people")
    p = add("token", cmd_token, "mint a console credential for a person")
    p.add_argument("person_id")
    p = add("assign", cmd_assign, "set owner / approver / auditor")
    p.add_argument("agent_id")
    p.add_argument("--owner")
    p.add_argument("--approver")
    p.add_argument("--auditor")
    p.add_argument("--by")

    add("watches", cmd_watches, "list standing watches")
    p = add("watch", cmd_watch_add, "add a watch: buy when price drops below N")
    p.add_argument("--under", required=True)
    p.add_argument("--destination", default="COR")
    p.add_argument("--category", default="flights")
    p.add_argument("--every", type=int, default=60)
    p.add_argument("--notify-only", action="store_true")
    p.add_argument("--by")
    add("tick", cmd_tick, "one watcher pass (this is what cron calls)")
    p = add("watch-daemon", cmd_watch_daemon, "poll on an interval in the foreground")
    p.add_argument("--every", type=int, default=30)
    p.add_argument("--ticks", type=int)
    add("cron", cmd_cron, "print crontab / Cloud Scheduler setup")

    add("offers", cmd_offers, "list the merchant catalog")
    p = add("price", cmd_price, "change an offer price (drives the watcher)")
    p.add_argument("offer_id")
    p.add_argument("amount")
    p = add("revoke", cmd_revoke, "revoke a mandate (kills the rail token too)")
    p.add_argument("--jti")
    p.add_argument("--by", default="Marta")
    p = add("mandate", cmd_mandate, "show mandate state and spend")
    p.add_argument("--jti")
    add("jwks", cmd_jwks, "the public keys a merchant verifies against")

    p = add("audit", cmd_audit, "the trail")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--mandate")
    p = add("verify", cmd_verify, "recompute every chain and sign a global root")
    p.add_argument("--per-chain", action="store_true", help="list each chain")
    p = add("runs", cmd_runs, "agent runs")
    p.add_argument("--limit", type=int, default=15)
    add("escalations", cmd_escalations, "pending escalations")
    p = add("resolve", cmd_resolve, "approve or reject an escalation")
    p.add_argument("escalation_id")
    p.add_argument("decision", choices=["approve", "reject"])
    p.add_argument("--by", default="Marta")
    p.add_argument("--sticky", action="store_true")
    p = add("relay", cmd_relay, "drain the outbox once (external delivery)")
    p.add_argument("--batch", type=int, default=50)
    p = add("relay-daemon", cmd_relay_daemon, "keep draining; run several at once")
    p.add_argument("--every", type=float, default=1.0)
    p.add_argument("--passes", type=int)
    add("limits", cmd_limits, "guardrails: quotas, rate buckets, counters, locks")
    p = add("mcp-check", cmd_mcp_check, "does a merchant's MCP honour the contract?")
    p.add_argument("--url", default=None)
    p = add("mcp-demo", cmd_mcp_demo, "buy from a real merchant MCP, through the gate")
    p.add_argument("--url", default=None)
    p.add_argument("--merchant", default="vuelaya", choices=["vuelaya", "mami"])
    p.add_argument("--destination", default="MDE")
    p.add_argument("--query", default="")
    p.add_argument("--currency", default="COP")
    p.add_argument("--revoke", action="store_true")
    p = add("merchants", cmd_merchants, "who the agent can reach, and what it refuses")
    p.add_argument("--vuelaya", default=None)
    p.add_argument("--mami", default=None)
    add("protocols", cmd_protocols, "how TryTrust maps onto AP2, ACP and friends")

    for action in sub._name_parser_map.values():
        action.add_argument("--token", default=None, help="console credential (or set TT_TOKEN)")

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except auth.AuthError as exc:
        sys.exit(
            f"refused [{exc.code}]: {exc.detail}\n"
            f"mint one with:  uv run python -m src.agent.cli token <person_id>"
        )
    except limits.LimitExceeded as exc:
        # A guardrail is a normal outcome, not a crash. Say which one and why.
        sys.exit(
            f"refused by a guardrail [{exc.code}]: {exc.detail}\n"
            f"see the ceilings with:  uv run python -m src.agent.cli limits"
        )


if __name__ == "__main__":
    main()
