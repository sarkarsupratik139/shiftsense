#!/usr/bin/env bash
# Rebuild the synthetic base data pack in basedata/ (small: 50 people, full: 120 people).
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m shiftsense.basepack build "$@"
