#!/usr/bin/env bash
# Run the automated test suite (about 7 minutes on SQLite). Writes JUnit XML to ../evidence if asked:
#   scripts/run_tests.sh --junitxml=test-results.xml
# On PostgreSQL (use an EMPTY test database; the tests create and drop schemas):
#   SHIFTSENSE_DATABASE_URL=postgresql://... SHIFTSENSE_PG_SCHEMA_FROM_DB=1 scripts/run_tests.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/pytest -q "$@"
