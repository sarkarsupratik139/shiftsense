#!/usr/bin/env bash
# Stage files from data/inbox/<kind>/ as the given account (e.g. hr, analyst); the running app applies them.
#   scripts/stage_drop_folder.sh hr
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m shiftsense.dropfolder run --as "$1"
