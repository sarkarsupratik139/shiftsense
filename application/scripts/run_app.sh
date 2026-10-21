#!/usr/bin/env bash
# Start ShiftSense locally (SQLite in data/). Run from application/.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
exec .venv/bin/python -m streamlit run app.py --server.port "${PORT:-8501}"
