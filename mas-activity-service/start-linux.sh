#!/usr/bin/env bash
# Local terminal start (Linux/macOS). Windows: use start-windows.bat.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "ERROR: .venv missing. Run: ./setup-linux.sh"
  exit 1
fi
if [[ ! -f mas-activity.env ]]; then
  echo "ERROR: mas-activity.env missing. Copy mas-activity.env.example → mas-activity.env"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source mas-activity.env
set +a

if [[ -z "${MAS_ACTIVITY_KEY:-}" || "${MAS_ACTIVITY_KEY}" == "change-me-activity-key" ]]; then
  echo "ERROR: set a real MAS_ACTIVITY_KEY in mas-activity.env"
  exit 1
fi

HOST="${MAS_ACTIVITY_HOST:-127.0.0.1}"
PORT="${MAS_ACTIVITY_PORT:-8200}"
mkdir -p data

echo "Starting MAS Activity at http://${HOST}:${PORT}"
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
