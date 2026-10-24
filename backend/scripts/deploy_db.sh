#!/usr/bin/env bash
# Copy the prepared LOCAL database to a hosted Postgres (e.g. Railway) and set it up there:
# the 17 tables (cleaned + seeded), per-role read-only logins, the agent_state database
# and the demo users, then run the data and permission tests against it.
#
# Usage: TARGET_DATABASE_URL='postgresql://...' backend/scripts/deploy_db.sh
# Passwords come from .env.production. Refuses to run against the company's source DB.
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; source .env; source .env.production; set +a
PATH="/opt/homebrew/opt/libpq/bin:$PATH"
: "${TARGET_DATABASE_URL:?Set TARGET_DATABASE_URL to the hosted Postgres URL}"

backend/.venv/bin/python - <<'PY'
import os, sys
from psycopg.conninfo import conninfo_to_dict
target, source = (conninfo_to_dict(os.environ[k]) for k in ("TARGET_DATABASE_URL", "SOURCE_DATABASE_URL"))
if (target.get("host"), target.get("port")) == (source.get("host"), source.get("port")):
    sys.exit("Refusing to run: TARGET_DATABASE_URL points at the company's source database.")
PY

dump="dumps/prepared_$(date +%Y%m%d_%H%M).dump"
mkdir -p dumps
pg_dump "$DATABASE_URL" --schema=public --no-owner --no-privileges -Fc -f "$dump"
echo "Dumped prepared local DB to $dump"

pg_restore -d "$TARGET_DATABASE_URL" --clean --if-exists --no-owner --no-privileges "$dump"
echo "Restored into target"

backend/.venv/bin/python backend/scripts/generate_db_roles.py
psql "$TARGET_DATABASE_URL" -q \
  -v role_inventory_password="$ROLE_INVENTORY_PASSWORD" \
  -v role_procurement_password="$ROLE_PROCUREMENT_PASSWORD" \
  -v role_owner_password="$ROLE_OWNER_PASSWORD" \
  -v role_procurement_writer_password="$ROLE_PROCUREMENT_WRITER_PASSWORD" \
  -v role_inventory_writer_password="$ROLE_INVENTORY_WRITER_PASSWORD" \
  -f backend/scripts/setup_db_roles.sql
echo "Applied per-role logins"

DATABASE_URL="$TARGET_DATABASE_URL" backend/.venv/bin/python backend/scripts/seed_users.py

echo "Checking data and permissions on the target"
(cd backend && DATABASE_URL="$TARGET_DATABASE_URL" DB_CONNECT_TIMEOUT=20 .venv/bin/python -m pytest -q tests/test_data_invariants.py tests/test_db_roles.py)
