# Decision records

Every real choice gets a numbered file here, written when the call is made —
while the rejected alternative is still remembered.
[`../../DECISIONS.md`](../../DECISIONS.md) stays the short index (one entry
per file, same four fields); this directory holds the full records with
context and consequences.

## When a record is REQUIRED

- You chose between two or more real alternatives (library, pattern, contract
  shape, vendor, failure semantics).
- You changed a frozen contract under [`../../contracts/`](../../contracts/) —
  the CI docs-guard **rejects a contract change without a decision record**
  in the same PR.
- You broke or narrowed one of the non-negotiable rules in
  [`../../../AGENTS.md`](../../../AGENTS.md).

## Workflow

1. Copy [`TEMPLATE.md`](TEMPLATE.md) to `NNNN-short-slug.md` (next free
   number — check this directory and the index).
2. Fill it. The **Does not solve** line is mandatory — it is the line the
   judges read and the line that keeps us honest.
3. Append the short version to [`../../DECISIONS.md`](../../DECISIONS.md).
4. Same PR as the change that triggered the decision, with a devlog entry
   linking back to it.

## Index

- [`0016-agent-orchestration-without-langgraph.md`](0016-agent-orchestration-without-langgraph.md) —
  the agent runs on our own graph: LangGraph's critical ideas, no LangGraph.
- [`0017-documentation-protocol.md`](0017-documentation-protocol.md) —
  devlogs + decision records, enforced by the CI docs-guard.
- Entries 1–15 predate this directory and live only in the
  [index](../../DECISIONS.md).
