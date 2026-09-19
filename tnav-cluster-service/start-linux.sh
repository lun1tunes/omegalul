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
# Do not source the env file here — passwords with spaces/"=" and a UTF-8 BOM break bash too.
# Python load_service_env (utf-8-sig) reads tnav-cluster.env, same as mas-activity-service.
echo "Starting tNav Cluster Agent. Env is loaded from tnav-cluster.env by Python, not by this script."
echo "Keep this terminal open. Ctrl+C to stop."
exec .venv/bin/python -m app
