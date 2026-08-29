# MCP handoff — what `trytrust-merchants` needs to expose

The agent side is done and tested over real transport. This is the other half.

## State

`apps/vuela-ya`, `apps/logistics` and `apps/mami` declare `mcp-handler` and
`@modelcontextprotocol/server` but contain no MCP route — 324 lines across all
three apps, all default Next.js scaffold. There is nothing to connect to yet.

Our side speaks MCP 2.x and is verified against a reference server:
`src/agent/ports/mcp_server.py` (the executable spec) and
`src/agent/ports/mcp_client.py` (what the agent uses).

## The contract — three tools, no more

Frozen in `contracts/schemas.md` §10. The signatures matter more than the
transport.

| Tool | Args | Returns | Effects |
|---|---|---|---|
| `search_offers` | `{origin?, destination?, date?, category?}` | `Offer[]` | none |
| `get_offer` | `{offer_id}` | `Offer` | none |
| `request_purchase` | `{offer_id, mandate_jti}` | `{status, purchase_id}` | calls the kernel; **never charges** |

`Offer` = `{offer_id, merchant_id, category, title, price, currency, origin,
destination, depart_date, description}` — `price` is a fixed 2-decimal string.

### Three rules that are not negotiable

1. **`request_purchase` takes no `amount`.** The price comes from the offer.
   This is why a hallucinated or injected number has nowhere to enter the
   system (S6). Adding an `amount` parameter breaks the security model.
2. **No payment tool, ever.** Nothing in the MCP surface may reach a payment
   rail. The only route to money is gate → verify → checkout (S2). Our client
   allow-lists the three tools above and ignores anything whose name suggests
   money, loudly.
3. **Outputs are data, not instructions.** Ship adversarial descriptions in the
   real catalog — the agent fences them and the gate refuses them. That demo is
   worth more than a clean catalog.

## The Next.js route

`apps/vuela-ya/app/api/mcp/route.ts`, using the `mcp-handler` already in
`package.json`:

```ts
import { createMcpHandler } from "mcp-handler";
import { z } from "zod";

const KERNEL = process.env.TRYTRUST_KERNEL_URL ?? "http://localhost:8080";

const handler = createMcpHandler(
  (server) => {
    server.tool(
      "search_offers",
      "Search the catalog. Read-only: costs nothing, commits nothing.",
      {
        origin: z.string().optional(),
        destination: z.string().optional(),
        date: z.string().optional(),
        category: z.string().optional(),
      },
      async (args) => {
        const offers = await searchCatalog(args);          // your data layer
        return { content: [{ type: "text", text: JSON.stringify(offers) }] };
      },
    );

    server.tool(
      "get_offer",
      "One offer by id. Read-only.",
      { offer_id: z.string() },
      async ({ offer_id }) => {
        const offer = await getOffer(offer_id);
        return { content: [{ type: "text", text: JSON.stringify(offer) }] };
      },
    );

    // NOTE: no `amount` argument. The price is ours, not the agent's.
    server.tool(
      "request_purchase",
      "Submit a purchase for decision. Never charges.",
      { offer_id: z.string(), mandate_jti: z.string() },
      async ({ offer_id, mandate_jti }) => {
        const res = await fetch(`${KERNEL}/purchases`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ offer_id, mandate_jti }),
        });
        return { content: [{ type: "text", text: await res.text() }] };
      },
    );
  },
  {},
  { basePath: "/api" },
);

export { handler as GET, handler as POST };
```

Tool results travel as a JSON string inside a text content block — that is what
our client parses, and what the reference server returns.

## Verifying it

Point the agent at the app and run the same check we run against the reference
server:

    TT_MCP_URL=http://localhost:3000/api/mcp \
      uv run python -m src.agent.cli mcp-check

That prints the tools the server advertises and whether the contract holds:
which of the three are missing, which are unexpected, and whether any tool name
suggests it moves money. Then:

    TT_MCP_URL=http://localhost:3000/api/mcp \
      uv run python -m src.agent.cli mcp-demo

which searches, buys inside the mandate, and then tries an injected listing —
which must be refused or escalated, never silently approved.

## Then the other two merchants

`logistics` and `mami` expose the same three tools against their own catalogs.
The agent fans out across every registered merchant; the mandate's
`scope.merchants` decides which of them a given buyer may actually purchase
from, so adding a merchant never widens anyone's permission.
