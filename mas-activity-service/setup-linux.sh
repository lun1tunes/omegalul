#!/usr/bin/env bash
# One-time local setup (Linux/macOS). Windows: setup-windows.bat
set -euo pipefail
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  uv pip install --python .venv/bin/python -r requirements.txt
else
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

if [[ ! -f mas-activity.env ]]; then
  cp mas-activity.env.example mas-activity.env
  echo "Created mas-activity.env — edit MAS_ACTIVITY_KEY before start."
fi
mkdir -p data
echo "OK. Next: ./start-linux.sh"
