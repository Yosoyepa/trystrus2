# Decision 0030 — The Rappi bridge is a merchant-rail, not a rail

**Status:** Accepted (2026-08-30) · **Owner:** Dev 2 · **Supersedes:** the
"vía 4 descartada" stance of
[`research/2026-08-30-dev2-rappi-bridge-analysis.md`](../research/2026-08-30-dev2-rappi-bridge-analysis.md)
for the execution layer only (the kernel-gated architecture is unchanged).

## Context

The happy-path merchant (Rappi) exposes no buyer-side API; the demo account
vaults the card and address inside a real Rappi session. A third-party client
(`@crafter/rappi-cli`, MIT) was audited (clone pinned at `vendor/`, npm
tarball verified identical) and proven in a live agentic run: a real order
(`2496728264`, COP 18,300) was placed end-to-end, with no CVC prompt at
checkout, confirming the web-internal API (`services.grability.rappi.com`)
works deterministically — search drifts from checkout (store re-resolution,
service fee), cart mutations are PUT-replace, stores enforce min-amount
server-side, and the checkout `return_key` binds a quoted cart to its
confirmation.

## Chose

1. **`src/rappi_bridge/`** — a Python guard bridge running **on the credential
   machine only** (the Rappi session token never leaves it). It ports the
   audited endpoints natively (httpx; no Bun dependency in production) and is
   the ONLY component allowed to touch Rappi. It enforces, by code:
   DRY_RUN default-on, a hardcoded COP cap, clean-cart precondition +
   PUT-replace semantics, delivery-address binding against the mandate,
   cent-exact drift rejection against the kernel-approved amount, and
   single-flight idempotency (SQLite checkpoint per step; a `clicked` state
   without confirmation is `uncertain` and is never re-clicked).
2. **Kernel-minted capture tokens** (`src/api/decision/capture_token.py`,
   `typ=capture-token+jwt`, ES256/Ed25519, TTL ≤ 120 s) binding
   `purchase_id + reservation_id + amount + cart_hash + dry_run`. The bridge
   verifies the token against the kernel JWKS and refuses to arm the checkout
   without a valid one — the human step-up approval is literally the key that
   unlocks the purchase click.
3. **The click is the capture.** Rappi vaults the card, so no payment rail
   (frozen #24) is involved; `place_order` completes
   `pending_capture → captured` and emits `purchase.captured`. DRY_RUN runs
   end in `dry_run_confirmed` with the reservation released, receipt labelled
   `dry_run: true`.
4. **Contract surface is additive**: `aval/contracts/rappi-bridge.yaml` (new
   file); `api.yaml` stays frozen. The CLI's own MCP server is never exposed
   to the agent (its raw `place_order` tool is an unguarded money path — S2).

## Rejected

Exposing `@crafter/rappi-cli`'s MCP/tools directly to the agent; uploading
the session token to Cloud Run; a Playwright/DOM bridge as primary (kept as
plan C if the undocumented API rotates its `app-version` header); client-side
price tolerance (drift = reject + re-quote; #23 weight products out of scope);
re-clicking an `uncertain` order (reconciliation via "Mis pedidos" instead).

## Why

A real run measured every failure the design predicted: search quote COP
9,400 vs checkout COP 10,300 (store re-resolution + hidden service fee),
search coords vs account-active address split-brain, and a server-side store
minimum rejecting an order with no money moved. Guarding these in the kernel
alone is not enough — the bridge is a second, independent enforcement point
on the machine that actually holds the money button.

## Does not solve

Undocumented-API drift (the `app-version` hash can rotate — smoke test before
each session); Rappi's fraud-check right to silently cancel orders (ToS §10);
purchases the owner makes outside the agent (the mandate ceiling covers what
the agent does, not the whole card); reconciliation of an `uncertain` order
beyond surfacing `bridge.reconciled` events for a human.

## Implementation map

| Piece | Path | Lane |
|---|---|---|
| Guard bridge service | `src/rappi_bridge/` | Dev 2 (this decision) |
| Capture token mint | `src/api/decision/capture_token.py` | Dev 2 |
| `MerchantBridge` port | `src/api/decision/ports.py` (additive) | Dev 2 |
| Kernel capture endpoint + wiring into `DecisionService` | `src/api/routers/` + `decision/service.py` | Dev 3 (brief) |
| `Quote` object, events (`rappi.quote.created`, `purchase.placed`, `bridge.price_drift`, `bridge.reconciled`, `purchase.dry_run_captured`) | `aval/contracts/rappi-bridge.yaml` | Dev 3 consumes |
