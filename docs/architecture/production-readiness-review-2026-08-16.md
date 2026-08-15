# MAS production-readiness review — 2026-08-16

## Verdict

**Control-plane + repository gates: GREEN.** Compose stack (postgres, excel-tools, n8n, n8n-runners, mas-activity) healthy; host health script PASS; all n8n Code smokes PASS; excel-agent-tools 70/70; mas-activity 16/16; commissioning integration cases 0–3 PASS.

**Not a full corporate production sign-off:** UI bindings/credentials, expert SCHEDULE RAG corpus, Phase-5 enterprise gates (SSO/RBAC/DR/load), and live Orchestrator E2E with LLM remain human-operated.

## What was fixed in this review

1. Activity HITL: `AWAITING_HUMAN` arms composer; Trace Writer handoffs no longer clear open gates.
2. Builder: commissioning well-identity only for date-like facts; grounded `source_map` (incl. `source_ref`); timeline keep/remove/new-well HITL.
3. Health Check form: live HTTP pings to excel-tools, n8n-runners, mas-activity, n8n `/healthz`.
4. Compose: `mas-activity` service + n8n healthcheck; host script `scripts/mas_stack_health.py`.
5. Smokes/contracts updated for Materialize uploads, HITL file fields, timeline template.

## Test matrix (executed)

| Suite | Result |
|---|---|
| `n8n/tests/*-smoke.js` (17) | PASS |
| `excel-agent-tools` pytest | 70 passed |
| `mas-activity-service` pytest | 16 passed |
| `scripts/mas_stack_health.py` | PASS (7/7) |
| `run_integration_cases.py` (case0–3) | PASS |

## Residual risks (accept for MVP / fix before hard prod)

- P1: Math service still unauthenticated if exposed; Activity GETs unauthenticated beyond localhost.
- P1: Trace Writer may still hardcode `ACTIVITY_KEY=dev-local` until env-bound in UI.
- P2: Clean volume wipe requires full UI re-import (Public API disabled by design).
- P2: Live Orch→Builder→Excel E2E still flaky on Planner structured output / verifier payload size.
- P3: Combat fixtures under `simulation-model-example/` are gitignored — keep CI copies if needed.

## Commands to re-run

```bash
cd /home/lun1z/omegalul
docker compose up -d --build
python3 scripts/mas_stack_health.py
export WORKSPACE_ROOT=$PWD
for f in n8n/tests/*-smoke.js; do node "$f" || exit 1; done
cd excel-agent-tools && pytest -q
cd ../mas-activity-service && PYTHONPATH=. pytest -q
python3 simulation-model-example/combat-dates-revise/run_integration_cases.py
```
