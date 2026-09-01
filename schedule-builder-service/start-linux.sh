#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "ERROR: .venv missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f schedule-builder.env ]]; then
  echo "ERROR: schedule-builder.env missing. Copy schedule-builder.env.example → schedule-builder.env"
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is required on PATH (commissioning / group-rebind timeline emit)."
  exit 1
fi
set -a
# shellcheck disable=SC1091
source schedule-builder.env
set +a
echo "Starting Schedule Builder at http://${SCHEDULE_BUILDER_HOST:-127.0.0.1}:${SCHEDULE_BUILDER_PORT:-8090}"
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host "${SCHEDULE_BUILDER_HOST:-127.0.0.1}" --port "${SCHEDULE_BUILDER_PORT:-8090}"
