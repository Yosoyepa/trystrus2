# Trust layer for purchases made by agents

From the whiteboard, 29 Aug 2026. Shared starting point for the build.

## Problem

Nobody trusts a transaction made by an agent. The merchant cannot check that the
person agreed, and the person cannot prove afterwards what they agreed to. So
merchants either block agents and lose real sales, or let them through as people
and absorb the fraud.

## What the platform has to get right

- **Traceability.** Every action leaves a record tying the purchase to the person, the permission and the rule.
- **Idempotency.** The same request sent twice buys once.
- **Usability.** Authorizing takes seconds from Telegram or WhatsApp.
- **Security.** The agent never holds the raw card — nobody here does. The instrument lives tokenized at the rail (PayPal vault); the agent holds only permission to use it.
- **Non-repudiation.** Neither side can later deny what they did.

Two limits of the agent shape the design:

- It lacks domain knowledge. It does not know what is normal for this buyer.
- It needs access to past transactions to know whether a purchase is a repeat and what it means.

## Flow

1. The buyer sends a request from web or Telegram. It crosses the auth boundary.
2. The BFF receives it and hands the work to the agent.
3. The agent searches and prices through the merchant's MCP server.
4. The agent proposes a purchase. It does not buy.
5. The business rules check the proposal against the buyer's authorization and the transaction history.
6. Inside the rules: it proceeds. Outside: the platform stops and asks the buyer to confirm. This is the human in the loop, and it is a stop, not a notification.
7. The purchase goes through the merchant API. The queue carries the events, the logs and the database record what happened.

## Components

All inside our VPC, except the merchant.

- **Auth.** The boundary. Nothing enters without an identity attached.
- **BFF.** One entry point for web, Telegram and WhatsApp.
- **Agent.** Searches, compares, proposes. No authority to move money.
- **Business rules.** Allow, ask, or refuse, with the reason recorded.
- **Database.** Authorizations, transactions, and the state the rules read from.
- **Queue.** Events between components, so a slow merchant blocks nothing else.
- **Logs.** The append record of what happened. Feeds the control tower.
- **Merchant MCP server.** How the agent explores what is for sale.
- **Merchant API.** How the purchase is made.

## Control tower

One view of every action the agents have taken: who authorized it, which rule
allowed it, what the agent proposed, whether a human confirmed it, and what the
merchant returned. It reads the same records the rules read.

Three audiences. The buyer sees what was bought and under which permission. The
merchant shows it verified the purchase before accepting. An auditor reconstructs
the sequence without trusting either.

## Use cases

- Flights
- Retail (Amazon, MercadoLibre)
- Logistics

## Since decided

The three questions below were open on the whiteboard. They are closed now —
decisions in [`../DECISIONS.md`](../DECISIONS.md), formats in
[`../contracts/schemas.md`](../contracts/schemas.md):

- **The authorization object.** An SD-JWT (RFC 9901) with AP2-shaped claims:
  limits, scope, validity window, an opaque payment-method reference, the
  agent's public key, and the conditions as JSON Logic. Signed by the buyer's
  passkey over the mandate's canonical hash; the agent proves possession with
  a key-bound, canonically-signed purchase intent (decisions #2, #3, #9).
- **Revocation.** A synchronous check at pay-time inside the charging
  transaction, plus DELETE of the rail payment token — the next attempt
  fails twice: once in our state, once at PayPal (decision #4).
- **Disputes.** A sandbox PayPal dispute (`UNAUTHORISED`) resolved with the
  hash-chained evidence bundle: mandate → signed intent → approval receipt →
  capture (decision #8).

**Where each whiteboard component landed:** Auth + BFF → `kernel/` (Dev 3
identity + Dev 2 decision) · Agent — own graph, no framework (decision #16)
→ `agent/` (Dev 1) · business rules → the deterministic gate inside
`kernel/` (Dev 2) · queue → Postgres outbox, no broker (decision #10) ·
logs → hash-chained `audit_events` with KMS-signed roots and an external
witness (decision #7) · merchant MCP server + payment API → `merchant/`
(Dev 3, PayPal sandbox rail) · control tower → `web/` (Dev 4).
