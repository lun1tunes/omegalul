#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
  echo "ERROR: .venv missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -f tnav-cluster.env ]]; then
  echo "ERROR: tnav-cluster.env missing. Copy tnav-cluster.env.example → tnav-cluster.env"
  exit 1
fi
set -a
# shellcheck disable=SC1091
source tnav-cluster.env
set +a
echo "Starting tNav Cluster Agent at http://${TNAV_CLUSTER_HOST:-127.0.0.1}:${TNAV_CLUSTER_PORT:-8400}"
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m uvicorn app.main:app --host "${TNAV_CLUSTER_HOST:-127.0.0.1}" --port "${TNAV_CLUSTER_PORT:-8400}"
