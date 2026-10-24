#!/usr/bin/env bash
# Rebuild the local working DB from the raw source dump. Safe to re-run; LOCAL DB ONLY.
#   restore dump -> clean_data.sql -> seed_extra_data.py -> per-role logins -> demo users -> invariant tests
# Usage: backend/scripts/prepare_local_db.sh [path/to/dump]   (default: newest in dumps/)
#        SKIP_SYNTHETIC=1 backend/scripts/prepare_local_db.sh   (cleaned real data only)
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; source .env; set +a
PATH="/opt/homebrew/opt/libpq/bin:$PATH"

case "$DATABASE_URL" in
  *localhost*|*127.0.0.1*) ;;
  *) echo "Refusing to run: DATABASE_URL is not a local database." >&2; exit 1 ;;
esac

dump="${1:-$(ls -t dumps/source_*.dump | head -1)}"
echo "Restoring $dump"
docker compose up -d --wait >/dev/null
pg_restore -d "$DATABASE_URL" --clean --if-exists --no-owner --no-privileges "$dump"

echo "Cleaning"
psql "$DATABASE_URL" -q -v as_of="${DATA_AS_OF:-2026-08-21} 23:59:59" -f backend/scripts/clean_data.sql

if [ "${SKIP_SYNTHETIC:-0}" = "1" ]; then
  echo "Skipping synthetic history"
else
  echo "Seeding synthetic history"
  backend/.venv/bin/python backend/scripts/seed_extra_data.py
fi

backend/scripts/apply_db_roles.sh

echo "Creating demo users"
backend/.venv/bin/python backend/scripts/seed_users.py

echo "Checking data invariants"
(cd backend && .venv/bin/python -m pytest -q tests/test_data_invariants.py)
