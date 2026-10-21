#!/usr/bin/env bash
# Backups: create | verify <file> | restore <file> | drill  (SQLite or PostgreSQL, whichever is configured)
#   scripts/backup.sh create --keep 14
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m shiftsense.backup "$@"
