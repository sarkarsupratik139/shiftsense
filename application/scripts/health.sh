#!/usr/bin/env bash
# Health check (exit code 1 when an essential check fails). Add --json for monitoring, --alert to email.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m shiftsense.health "$@"
