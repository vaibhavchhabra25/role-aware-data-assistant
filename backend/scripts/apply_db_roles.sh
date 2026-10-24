#!/usr/bin/env bash
# Create/refresh the per-role read-only Postgres logins on the LOCAL database.
# Regenerates setup_db_roles.sql from roles.yaml, then applies it with passwords from .env.
# Usage: backend/scripts/apply_db_roles.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; source .env; set +a
PATH="/opt/homebrew/opt/libpq/bin:$PATH"

case "$DATABASE_URL" in
  *localhost*|*127.0.0.1*) ;;
  *) echo "Refusing to run: DATABASE_URL is not a local database." >&2; exit 1 ;;
esac

backend/.venv/bin/python backend/scripts/generate_db_roles.py
psql "$DATABASE_URL" -q \
  -v role_inventory_password="$ROLE_INVENTORY_PASSWORD" \
  -v role_procurement_password="$ROLE_PROCUREMENT_PASSWORD" \
  -v role_owner_password="$ROLE_OWNER_PASSWORD" \
  -v role_procurement_writer_password="$ROLE_PROCUREMENT_WRITER_PASSWORD" \
  -v role_inventory_writer_password="$ROLE_INVENTORY_WRITER_PASSWORD" \
  -f backend/scripts/setup_db_roles.sql
echo "Applied DB roles"
