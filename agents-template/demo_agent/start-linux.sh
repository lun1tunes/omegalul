#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "ERROR: .venv missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f demo-agent.env ]]; then
  echo "ERROR: demo-agent.env missing. Copy demo-agent.env.example → demo-agent.env"
  exit 1
fi
set -a
# shellcheck disable=SC1091
source demo-agent.env
set +a
echo "Starting Demo Agent at http://${DEMO_AGENT_HOST:-127.0.0.1}:${DEMO_AGENT_PORT:-8300}"
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host "${DEMO_AGENT_HOST:-127.0.0.1}" --port "${DEMO_AGENT_PORT:-8300}"
