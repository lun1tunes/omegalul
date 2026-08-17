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

mkdir -p data
echo "Starting MAS Activity. Env is loaded from mas-activity.env by Python."
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m app
