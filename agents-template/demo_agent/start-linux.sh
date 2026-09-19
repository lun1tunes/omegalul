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
# Do not source the env file here — Python load_service_env (utf-8-sig) reads it.
echo "Starting Demo Agent. Env is loaded from demo-agent.env by Python, not by this script."
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m app
