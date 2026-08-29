#!/usr/bin/env bash
# docs-guard — documentation obligations for this repo (decision 0017).
#
#   1. Code under the service dirs without a devlog change        -> REJECT
#   2. Frozen contracts changed without a decision-record change  -> REJECT
#
# Usage: scripts/docs-guard.sh [base-ref]     (default: origin/main)
# Runs in CI on every PR; also usable locally before pushing
# (includes uncommitted changes when run against a base ref).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

BASE="${1:-origin/main}"
# Union of committed (base...HEAD), working-tree (base) and untracked diffs:
# works in CI (clean tree) and locally (uncommitted/new files included).
CHANGED=$({ git diff --name-only "$BASE"...HEAD 2>/dev/null || true
            git diff --name-only "$BASE" 2>/dev/null || true
            git ls-files --others --exclude-standard; } | sort -u)
[ -z "$CHANGED" ] && { echo "docs-guard: no changes against $BASE"; exit 0; }

CODE=$(echo "$CHANGED" | grep -E '^((aval/)?(kernel|agent|merchant|web|packages|services|infra|src)/).*\.(py|ts|tsx|js|jsx|sh|sql|go|toml)$' || true)
CONTRACTS=$(echo "$CHANGED" | grep -E '^aval/contracts/' || true)
DEVLOGS=$(echo "$CHANGED" | grep -E '^aval/docs/devlogs/' || true)
DECISIONS=$(echo "$CHANGED" | grep -E '^aval/docs/decisions/' || true)

status=0
if [ -n "$CODE" ] && [ -z "$DEVLOGS" ]; then
  echo "FAIL docs-guard: code changed but no devlog entry was touched."
  echo "      Append an entry to your workstream's aval/docs/devlogs/<A|B|C1|C2|C3|D>.md in this same PR (decision 0017)."
  status=1
fi
if [ -n "$CONTRACTS" ] && [ -z "$DECISIONS" ]; then
  echo "FAIL docs-guard: frozen contract changed but no decision record was touched."
  echo "      Contracts change by decision: add aval/docs/decisions/NNNN-*.md + index entry in DECISIONS.md (decision 0017)."
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "OK docs-guard: documentation obligations met against $BASE"
fi
exit "$status"
