# MCP integration — tested against the real merchants

Tested 30 Aug 2026 against `trytrust-merchants@6bbd91e`, both apps running
locally. Our client reads both servers correctly. Two findings, one blocking.

## What works

`apps/vuela-ya` and `apps/mami` are real: 756 flights and 130k seats seeded,
a full REST surface, and MCP servers with eight and six tools. Our client
enumerates both, calls their read tools, and parses their responses without
adaptation.

| vuela-ya | mami |
|---|---|
| `list_airports` `search_flights` `compare_flights` `get_flight_details` `get_seat_map` `select_seat` `release_seat` `pay` | `list_products` `search_products` `add_to_cart` `remove_from_cart` `review_cart` `pay` |

Their tool vocabulary is richer than the three generic tools in
`contracts/schemas.md` §10, and that is an improvement, not a problem — seat
maps and cart review are real commerce. The agent should adapt to each
merchant's vocabulary. What cannot move is the boundary below.

## Finding 1 — every tool registration throws (fixed, patch attached)

`POST /api/mcp` returned **500 to every request**, including `initialize`.

`mcp-handler@2.1.1` is built against `@modelcontextprotocol/server@2.0.0` (v2)
and hands the callback a v2 `McpServer`. `lib/mcp/server.ts` imports `McpServer`
from `@modelcontextprotocol/sdk@1.30.0` (v1) and calls the v1 four-argument
form. The v2 object has no `.tool()`:

    server.tool is not a function

So all fourteen registrations threw and initialization failed. This was not
caught because `scripts/test-mcp.ts` tests the service functions directly and
never exercises MCP over HTTP — the transport had never actually run.

The fix is mechanical, and zod 4 works fine once it is applied:

```ts
// before — v1 signature
server.tool('list_airports', 'List served airports', { query: z.string().optional() }, handler);

// after — v2 signature
server.registerTool(
  'list_airports',
  { description: 'List served airports', inputSchema: { query: z.string().optional() } },
  handler,
);
```

`aval/docs/mcp-v2-registerTool.patch` converts all fourteen tools across both
apps. **It is applied to the working tree of `trytrust-merchants` but not
committed** — it is your repo, so the commit is yours to make:

    cd trytrust-merchants && git apply --check ../Hackthon-Yuno-Nauta-/aval/docs/mcp-v2-registerTool.patch

After it, `initialize`, `tools/list` and `tools/call` all return 200.

## Finding 2 — `pay` bypasses the entire trust layer (blocking)

Both servers expose a `pay` tool that settles directly. It takes a booking
session and passenger details. It does not take a mandate, does not verify a
signature, and does not call the kernel.

Demonstrated against the running app, with no mandate anywhere in the flow:

    select_seat  -> held 1A, session 5dba4c3b-…
    pay          -> "Payment processed successfully. Flight booking confirmed!"
    booking ref  -> VY-FA982E
    status       -> confirmed
    passenger    -> "Nobody Authorised This"

That is a confirmed booking created by an agent nobody authorised. It makes
TryTrust optional: any agent that can reach the MCP endpoint can buy, and the
mandate, the gate, the escalation and the audit trail are all decoration.

This is property **S2 — exactly one path to money** — and it is the claim the
whole project rests on.

### The fix

`pay` must require a mandate and defer the decision to the kernel:

```ts
server.registerTool(
  'pay',
  {
    description: 'Finalize the booking. Requires an authorised mandate.',
    inputSchema: {
      booking_session_id: z.string(),
      mandate_jti: z.string(),          // ← required, not optional
      intent_jws: z.string(),           // ← the agent's signature over this purchase
    },
  },
  async ({ booking_session_id, mandate_jti, intent_jws }) => {
    // The merchant verifies the mandate itself against our published JWKS,
    // then asks the kernel for live state and a budget reservation. Only an
    // APPROVED answer may settle.
    const verify = await fetch(`${KERNEL}/mandates/${mandate_jti}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ booking_session_id, intent_jws }),
    }).then((r) => r.json());

    if (verify.decision !== 'APPROVED') {
      return {
        isError: true,
        content: [{ type: 'text', text: JSON.stringify({ refused: verify.reason_code }) }],
      };
    }
    return executePayment({ booking_session_id, /* … */ });
  },
);
```

Three rules behind that, in order of how much they matter:

1. **No settlement without an APPROVED verify.** A `pay` tool that decides for
   itself is a second path to money, and there is only ever one.
2. **The price is the merchant's.** The agent may not supply an amount; the
   kernel checks the amount against your catalog. That is why an injected or
   hallucinated number has nowhere to enter.
3. **Tool output is data, not instructions.** Ship adversarial product and
   flight descriptions deliberately — our client fences them and the gate
   refuses them, and that demo is worth more than a clean catalog.

Until `pay` is gated, our client keeps it on a deny-list: any tool whose name
suggests money is reported and never called.

## Finding 3 — currency

Flights are priced in COP (`price: 150000`). Mandates are USD, and decision #8
records that the sandbox rail is USD-only. Either the merchant exposes a USD
price alongside COP, or mandates gain a COP scope and the rail choice changes.
Worth deciding before the demo rather than during it.

## Running it

    cd trytrust-merchants && pnpm install
    cd apps/vuela-ya && pnpm dev --port 3000     # seeds automatically
    cd apps/mami     && pnpm dev --port 3001

    TT_MCP_URL=http://localhost:3000/api/mcp uv run python -m src.agent.cli mcp-check
    TT_MCP_URL=http://localhost:3001/api/mcp uv run python -m src.agent.cli mcp-check

`mcp-check` reports which tools a server offers, which of ours are missing, and
whether any tool name suggests it moves money. It exits non-zero when the
contract does not hold, so it works as a CI gate.

## Next on our side

An adapter per merchant, mapping their vocabulary onto the agent's graph:
`search_flights` → offers, `select_seat` + `pay` → a single gated purchase.
Their tools stay as they are; the mandate's `scope.merchants` still decides who
may buy from whom, so adding a merchant never widens anyone's permission.
