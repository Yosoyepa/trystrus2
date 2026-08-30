# Guardrails and containment

Four layers. Each assumes the one above it has already failed.

    1. STRUCTURAL   the attack cannot be expressed
    2. QUOTA        if expressed, it is bounded
    3. CONTAINMENT  if it runs, it cannot reach anything
    4. EVIDENCE     whatever happened is on the record

## 1. Structural — the attack has no verb

The model's entire surface is three tools:

    search_offers(origin?, destination?, date?, category?)   read-only
    get_offer(offer_id)                                      read-only
    request_purchase(offer_id, mandate_jti)                  submits, never charges

There is no tool that creates a watch, sets a polling interval, spawns a run,
changes a limit, or reaches the payment rail. So *"poll the merchant every
0.01 s"* is not a thing a prompt can say — there is no verb for it. Watches are
created by a human through the console; the model never sees that path.

`request_purchase` takes no `amount`, so an injected price has nowhere to go.

This is the layer that actually stops the attack you named. Everything below
exists because one day a layer fails.

## 2. Quota — `src/agent/limits.py`

For a compromised console, a bug in our own loop, or a human who types
`--every 0`. Every number is overridable with a `TT_*` environment variable;
see them live with `uv run python -m src.agent.cli limits`.

| Guard | Default | Stops |
|---|---|---|
| `min_watch_interval_s` | 30 | a hot polling loop against the merchant |
| `max_watches_per_mandate` / `_total` | 10 / 200 | watch flooding |
| `max_watches_per_tick` | 25 | one tick becoming unbounded work |
| `merchant_calls_per_s` / burst | 2.0 / 20 | hammering the merchant, per agent |
| `llm_calls_per_s` / burst | 0.5 / 10 | inference spend spikes |
| `llm_calls_per_day` | 2000 | an overnight bill |
| `max_runs_per_agent_hour` | 60 | run storms |
| `max_steps_per_run` | 24 | a graph that loops |
| `max_run_seconds` | 120 | a graph that hangs |
| `max_offers_in_prompt` / `max_offer_text_chars` | 12 / 400 | a hostile catalog flooding the context |
| `max_ontology_chars` | 4000 | a hostile ontology doing the same |
| `max_escalations_per_hour` | 12 | drowning the one human who can say no |
| `tick_lock_ttl_s` | 55 | overlapping cron ticks (superseded by advisory locks) |

Two properties hold across all of them:

- **Exhaustion fails closed.** A throttled run ends `denied`, never `captured`.
  Running out of budget is never a reason to skip a check.
- **Counters live in the database.** Restarting the process does not hand an
  attacker a fresh budget.

Every trip writes an audit event (`watch.throttled`, or a node event carrying
`guardrail: true`), so throttling is visible in the control tower rather than
silent.

### The cron overlap problem

A one-minute schedule with a ninety-second job becomes two jobs, then three,
then a stampede. `limits.single_flight` takes a **Postgres advisory lock**: a second tick sees it
and **skips** — it does not queue, because a queued backlog is the stampede with
extra steps. No TTL is needed, because the lock dies with the session: a crashed
holder releases immediately rather than wedging the system until a timeout
expires. The lock is taken on a connection of its own, because advisory locks
are re-entrant within a session and a process must not be able to grant itself
a lock it already holds.

    $ uv run python -m src.agent.cli tick     # while another tick runs
    {"skipped": "another tick holds the lock", "watches_checked": 0}

## 3. Containment — the sandbox

Assume something got through anyway. How far can it get?

    deploy/sandbox.sh tick
    deploy/sandbox.sh ask "flight to Cordoba"

bubblewrap: read-only interpreter and repo, `var/` the only writable path, no
home directory, private PID/IPC/UTS namespaces, dies with its parent.

Measured, not asserted:

| | unsandboxed | sandboxed |
|---|---|---|
| `~/.ssh` | **readable (6 entries)** | blocked |
| `.env` | reachable | blocked |
| write to `$HOME` | **persists** | lands on ephemeral tmpfs, gone on exit |
| modify `src/agent/kernel.py` | **succeeds** | `OSError`, read-only |

That last row is the one that matters: **inside the sandbox the agent cannot
edit the code that decides whether it may spend.** Enforcement is not just
logically out of the model's reach, it is out of the process's reach.

For a machine that must survive a reboot, `trytrust-watch.service` carries the
systemd equivalent — `ProtectSystem=strict`, `ProtectHome=yes`,
`ReadWritePaths=` only `var/`, `NoNewPrivileges`, an empty capability set, a
`@system-service` syscall filter, plus `MemoryMax=512M`, `TasksMax=32`,
`CPUQuota=50%` and `TimeoutStartSec=50` so a hung tick is killed rather than
left to overlap.

### Egress

Every outbound call the agent makes goes through the model client or the MCP
transport, and both check `net.check()` first. The allowlist defaults to the
model API and localhost, and grows only from configured URLs
(`TT_ALLOWED_HOSTS`, `LLM_BASE_URL`, `TT_*_MCP_URL`, `TT_WEBHOOK_URL`). An
injected instruction that talks the agent into calling an attacker's URL fails
there and leaves an `egress.denied` event (test G9).

This is not network-level control: a compromised process can still open a
socket directly. VPC egress rules — Cloud Run with a serverless connector and
Cloud NAT restricted to the known domains — remain the production answer. What
changed is that the in-process path is enforced rather than promised.

## 4. Evidence

Whatever survives the first three layers still lands in the hash chain, with the
agent version pinned to the run, and `audit_events` refusing `UPDATE` and
`DELETE` at the database level.

## Verify it

    uv run python -m src.agent.tests        # G1-G8 cover this file
    uv run python -m src.agent.cli limits   # live budgets, counters, locks

## Honest gaps

- **Bearer tokens have no rotation or expiry.** A leaked token works until
  someone reissues. There is no revocation list.
- **The egress allowlist is in-process**, as described above.
- **A settling tool on the merchant side is still ungated.** Both merchant MCP
  servers expose `pay` with no mandate check. Our agent refuses to call it, so
  *our* agent cannot buy unauthorised — but any other client can. Fixing that
  is `aval/docs/MCP-HANDOFF.md` and belongs to the merchants repo.
- **Rate limits key on the agent and the mandate.** Minting agent ids no longer
  buys a fresh budget, but nothing ties buckets to a person across mandates.
