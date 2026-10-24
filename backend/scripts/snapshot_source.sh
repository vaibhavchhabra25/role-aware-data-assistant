#!/usr/bin/env bash
# Dump the 17 original tables from the read-only source DB, then rebuild the
# local working DB from that dump (prepare_local_db.sh). Usage: backend/scripts/snapshot_source.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
set -a; source .env; set +a
PATH="/opt/homebrew/opt/libpq/bin:$PATH"

tables=()
for t in $(backend/.venv/bin/python -c "import sys; sys.path.insert(0, 'backend/scripts'); from source_db import SOURCE_TABLES; print(*SOURCE_TABLES)"); do
  tables+=(-t "public.$t")
done

mkdir -p dumps
dump="dumps/source_$(date +%Y%m%d_%H%M).dump"
PGOPTIONS="-c default_transaction_read_only=on" \
  pg_dump "$SOURCE_DATABASE_URL" "${tables[@]}" --no-owner --no-privileges -Fc -f "$dump"
echo "Dumped to $dump"

backend/scripts/prepare_local_db.sh "$dump"
