#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "ERROR: .venv missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f math-service.env ]]; then
  echo "ERROR: math-service.env missing. Copy math-service.env.example → math-service.env"
  exit 1
fi
set -a
# shellcheck disable=SC1091
source math-service.env
set +a
echo "Starting Math Service at http://${MATH_SERVICE_HOST:-127.0.0.1}:${MATH_SERVICE_PORT:-8100}"
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host "${MATH_SERVICE_HOST:-127.0.0.1}" --port "${MATH_SERVICE_PORT:-8100}"
