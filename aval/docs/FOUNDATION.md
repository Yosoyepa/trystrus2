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
- **Usability.** Authorizing takes seconds from Slack or Telegram.
- **Security.** The agent never holds the raw card, only permission to use one the platform keeps.
- **Non-repudiation.** Neither side can later deny what they did.

Two limits of the agent shape the design:

- It lacks domain knowledge. It does not know what is normal for this buyer.
- It needs access to past transactions to know whether a purchase is a repeat and what it means.

## Flow

1. The buyer sends a request from Slack, web or Telegram. It crosses the auth boundary.
2. The BFF receives it and hands the work to the agent.
3. The agent searches and prices through the merchant's MCP server.
4. The agent proposes a purchase. It does not buy.
5. The business rules check the proposal against the buyer's authorization and the transaction history.
6. Inside the rules: it proceeds. Outside: the platform stops and asks the buyer to confirm. This is the human in the loop, and it is a stop, not a notification.
7. The purchase goes through the merchant API. The queue carries the events, the logs and the database record what happened.

## Components

All inside our VPC, except the merchant.

- **Auth.** The boundary. Nothing enters without an identity attached.
- **BFF.** One entry point for Slack, web and Telegram.
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

## Not decided yet

- The authorization object itself: what the buyer signs, its fields, how the agent proves it holds it, how the merchant checks it.
- Revocation: the buyer withdraws permission and the next attempt fails.
- Disputes: what the records answer when the buyer says they never authorized a purchase.
