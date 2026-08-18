#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "ERROR: .venv missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f excel-tools.env ]]; then
  echo "ERROR: excel-tools.env missing. Copy excel-tools.env.example → excel-tools.env"
  exit 1
fi
set -a
# shellcheck disable=SC1091
source excel-tools.env
set +a
mkdir -p data/sessions
echo "Starting Excel tools at http://${EXCEL_TOOLS_HOST:-127.0.0.1}:${EXCEL_TOOLS_PORT:-8000}"
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host "${EXCEL_TOOLS_HOST:-127.0.0.1}" --port "${EXCEL_TOOLS_PORT:-8000}"
