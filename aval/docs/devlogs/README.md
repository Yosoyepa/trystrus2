# Devlogs — one file per workstream, append-only

Each workstream keeps a dated log of what it built, why, and what it decided.
This is how four people — and any AI agent — build asynchronously without
duplicating work or losing context: **before starting a task, read the last
three entries of YOUR file and scan the others' headings.** The Open questions
line at the end of each entry is the cross-workstream radio.

## The four lanes (decision #19 — cut by capability)

| File | Workstream |
|---|---|
| `dev1.md` | Dev 1 · Agentic: agent graph, watcher, injection suite |
| `dev2.md` | Dev 2 · Fraud, contracts, idempotency: gate, verify, saga, ledger |
| `dev3.md` | Dev 3 · API backend: mandates, passkeys, merchant, PayPal rail |
| `dev4.md` | Dev 4 · Front & platform: consoles, bot, GCP infra, CI/CD |

## Rules

1. One file per workstream (list above). Append at the top (newest first).
   Never edit or delete old entries.
2. Entry format (copy it):

   ```
   ## YYYY-MM-DD HH:MM — <what I did, imperative>
   - **Why:** the problem this solves, in one or two lines
   - **Decision:** link `../decisions/NNNN-*.md`, or "none needed"
   - **Contracts touched:** `api.yaml#paths/...` / `schemas.md §N` / none
   - **Tests:** T## green / pending / none
   - **Open questions:** what the next person (or agent) picking this up hits
   ```

3. A PR that changes code without appending to a devlog is **rejected by CI**
   (`scripts/docs-guard.sh`). Presence is checked automatically; honesty is
   checked in review.
4. If you hit — or solved — something another workstream will face later, say
   it in **Open questions**. Two streams re-solving the same problem is the
   exact failure this directory exists to prevent.
5. Decisions go in `../decisions/` (full record) + `../../DECISIONS.md`
   (index). The devlog links to them; it does not replace them.
