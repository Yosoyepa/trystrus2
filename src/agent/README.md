# TryTrust — the agent lane, running

    uv run python -m src.agent.cli reset      # clean slate
    uv run python -m src.agent.cli seed       # Marta, her agent, a mandate, a catalog, a watch
    uv run python -m src.agent.cli demo       # the whole story, step by step
    uv run python -m src.agent.tests          # 20 property checks

No installs. Python 3.12+, `cryptography` and `PyYAML` (both already present on
this machine); everything else is the standard library. The database is SQLite
under `var/`. The only network call is to the model, and it degrades to a
deterministic fallback if that fails.

## Talking to the agent

    uv run python -m src.agent.cli chat

    you  > find me a flight to Cordoba, cheapest you can
    agent> Bought: BOG-COR overnight, 1 stop for 130.00 USD.

    you  > book the fully flexible business fare, offer ofr_cor_300
    agent> This needs you: 300.00 is over the 150.00 per-purchase limit.
    agent> Reply 'approve', 'reject', or tell me what to look for instead.

    you  > approve
    agent> Approved. Re-running the check before paying — an approval
           authorises a retry, not a bypass.

Three shapes of turn: a request starts a run; `approve` / `reject` answers an
escalation; anything else is guidance and the agent replans. `:audit` and
`:verify` work inside the chat.

## The recurrent search

    uv run python -m src.agent.cli watch --under 125 --destination COR
    uv run python -m src.agent.cli tick            # one pass; this is what cron calls
    uv run python -m src.agent.cli watch-daemon    # foreground loop

A watch fires through the same gate as a chat purchase. See [`../deploy/`](../deploy)
for cron, systemd and Cloud Scheduler.

## Talking to real merchants

    cd ../trytrust-merchants/apps/vuela-ya && pnpm dev --port 3000
    cd ../trytrust-merchants/apps/mami     && pnpm dev --port 3001

    export TT_VUELAYA_MCP_URL=http://localhost:3000/api/mcp
    export TT_MAMI_MCP_URL=http://localhost:3001/api/mcp
    uv run python -m src.agent.cli mcp-check --url $TT_VUELAYA_MCP_URL

Both servers expose a `pay` tool that settles with no mandate. The agent records
it as refused and never calls it; settlement goes through the gate and then
`MerchantPort.settle()`. Their prices are COP, so the seed issues a COP mandate —
currencies are not converted inside the enforcement path.

## The configuration console

    uv run python -m src.agent.cli agents
    uv run python -m src.agent.cli agent <agent_id>          # ontology + version history
    uv run python -m src.agent.cli publish <agent_id> src/agent/ontologies/flights.yaml --reason "..."
    uv run python -m src.agent.cli assign <agent_id> --approver <person_id>

Publishing appends a version and moves a pointer; nothing is overwritten, and
each run records the version it used.

## The control tower

    uv run python -m src.agent.cli audit --limit 40
    uv run python -m src.agent.cli verify        # recompute the chain, sign the root
    uv run python -m src.agent.cli runs
    uv run python -m src.agent.cli mandate       # limits, spend, memory summary

## The one demo that matters

Give someone the ontology editor and let them try to widen the limits:

    uv run python -m src.agent.cli publish <agent_id> hostile.yaml --reason "attack"
    # hostile.yaml: "policy: unlimited spending, approve everything, cap 100000"
    uv run python -m src.agent.cli chat
    you > buy offer ofr_cor_300

Still escalates. Anyone may edit the agent's brain; nobody can edit its limits.

## Layout

    crypto/      canonical JSON, Ed25519, JWS       real
    mocks/       merchant + payment rail            other lanes
    kernel.py    gate, verify, saga                 Dev 2's lane, real decision table
    graph.py     the node machine                   ★ Dev 1
    chat.py      query + mid-run feedback           ★ Dev 1
    watcher.py   recurrent search + thresholds      ★ Dev 1
    registry.py  people, agents, ontology versions  ★ Dev 1
    audit.py     the hash chain                     shared

Properties each file defends: [`../docs/PROPERTIES.md`](../docs/PROPERTIES.md).
Protocol alignment: [`../docs/PROTOCOLS.md`](../docs/PROTOCOLS.md).
