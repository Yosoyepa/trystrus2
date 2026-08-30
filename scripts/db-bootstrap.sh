#!/usr/bin/env bash
# Create (or recreate) the local `aval` database and load the seed DDL.
#
# Works with whichever Postgres you have: a local server (Homebrew, Postgres.app)
# or the docker-compose one. Not everyone on the team has Docker, and the demo
# must not depend on who does.
#
# Usage:
#   scripts/db-bootstrap.sh            # create if missing, load schema
#   scripts/db-bootstrap.sh --reset    # drop and recreate first
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

DB="${AVAL_DB_NAME:-aval}"
SCHEMA="aval/contracts/fixtures/schema.sql"
RESET=false
[ "${1:-}" = "--reset" ] && RESET=true

if command -v docker >/dev/null 2>&1 && docker compose ps db >/dev/null 2>&1; then
  echo "==> docker-compose Postgres"
  PSQL=(docker compose exec -T db psql -U aval)
  TARGET=(docker compose exec -T db psql -U aval -d "$DB")
elif command -v psql >/dev/null 2>&1; then
  echo "==> local Postgres ($(psql --version))"
  PSQL=(psql -d postgres)
  TARGET=(psql -d "$DB")
else
  echo "No Postgres found. Install one, or run: docker compose up -d db" >&2
  exit 1
fi

if [ "$RESET" = true ]; then
  echo "--> dropping $DB"
  "${PSQL[@]}" -c "DROP DATABASE IF EXISTS $DB" >/dev/null
fi

if ! "${PSQL[@]}" -lqt | cut -d'|' -f1 | grep -qw "$DB"; then
  echo "--> creating $DB"
  "${PSQL[@]}" -c "CREATE DATABASE $DB" >/dev/null
else
  echo "--> $DB exists"
fi

echo "--> loading $SCHEMA (idempotent: CREATE TABLE IF NOT EXISTS)"
"${TARGET[@]}" -q -v ON_ERROR_STOP=1 -f "$SCHEMA"

echo
"${TARGET[@]}" -c "\dt"
echo "OK. Connection string:"
echo "  postgresql+asyncpg://\${USER}@localhost/$DB    (local)"
echo "  postgresql+asyncpg://aval:aval@localhost:5433/$DB  (docker-compose)"
