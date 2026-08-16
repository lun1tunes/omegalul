# MAS production-readiness review — 2026-08-16 (re-run)

## Verdict

**Control-plane + repository gates: GREEN for MVP slice.**  
Compose stack healthy (postgres, excel-tools, n8n, n8n-runners, mas-activity); host `scripts/mas_stack_health.py` **PASS 7/7**; n8n Code smokes **18/18 PASS**; Activity pytest **46 PASS**; excel-tools image suite **18 PASS**; commissioning integration cases **0–3 PASS**; Activity UI smoke shows **`act_combat_case0`…`case3`** as **completed**.

**Not a full corporate production sign-off:** live Orch→Excel→Builder E2E with LLM/RAG, SSO/RBAC/DR/load, and expert SCHEDULE corpus remain human-operated. Health Check Form still needs UI bindings (PASS_WITH_TODO by design).

## What this re-run verified / fixed

1. **Stack:** `mas-activity` container was stopped while host uvicorn held `:8200` → Docker DNS health FAIL. Restarted compose `mas-activity`; health **PASS**.
2. **Health Check WF** already live-pings `excel-tools`, `n8n-runners`, `mas-activity`, `n8n` (+ DT/Orch/Trace probes). Host script also pings postgres.
3. **Activity:** prior session fixes (404-only not-found UI; prune only when list `count` ≤ returned page) rebuilt into image.
4. **Combat → UI:** `publish_combat_to_activity.py` publishes cases as `act_combat_*` (survive DT prune); wired into `run_integration_cases.py` (`PUBLISH_ACTIVITY=0` to skip). Softened DT-miss flash for presentation tasks.
5. **Browser smoke:** rail shows 4 combat tasks; case0/case3 open with `status: completed` and VERIFIED turns.

## Test matrix (executed this run)

| Suite | Result |
|---|---|
| `scripts/mas_stack_health.py` | PASS (7/7) |
| `n8n/tests/*-smoke.js` (18) | PASS |
| `mas-activity-service` pytest | 46 passed |
| `excel-agent-tools` Docker `--target test` | 18 passed |
| `excel-agent-tools` `test_workflow_contracts` | 44 passed |
| `run_integration_cases.py` (case0–3) | PASS |
| Activity UI browser (4 combat tasks) | PASS |

## Residual risks (accept for MVP)

- P1: Live Orchestrator E2E with LLM still flaky (Planner structured output / payload size).
- P1: Math service unauthenticated if exposed; Activity GETs unauthenticated beyond localhost.
- P2: Clean volume wipe ⇒ full UI re-import.
- P2: Combat fixtures often gitignored — keep CI copies.
- P3: `test_workflow_contracts` needs repo-root PYTHONPATH; run separately when validating portability.

## Commands

```bash
cd /home/lun1z/omegalul
docker compose up -d --build
python3 scripts/mas_stack_health.py
export WORKSPACE_ROOT=$PWD
for f in n8n/tests/*-smoke.js; do node "$f" || exit 1; done
cd mas-activity-service && PYTHONPATH=. .venv/bin/pytest -q
cd ../excel-agent-tools && docker build --target test -t omegalul-excel-tools-test . && docker run --rm omegalul-excel-tools-test
python3 simulation-model-example/combat-dates-revise/run_integration_cases.py
# UI: http://127.0.0.1:8200/ — act_combat_case0…3
```
