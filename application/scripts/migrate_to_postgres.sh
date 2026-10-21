#!/usr/bin/env bash
# Copy an existing SQLite database into PostgreSQL (backup first, one transaction, audit chain verified).
#   SHIFTSENSE_DATABASE_URL=postgresql://user:pass@host:5432/shiftsense scripts/migrate_to_postgres.sh [--replace]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m shiftsense.migrate_to_pg "$@"
