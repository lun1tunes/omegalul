# Petroleum Engineering MAS

Канонический UI-runbook и схема runtime: **[`docs.md`](docs.md)**.

| Артефакт | Назначение |
|---|---|
| [`docs.md`](docs.md) | импорт n8n 2.30.8, Data Tables, bindings, Health Check, smoke |
| [`n8n/import-manifest.json`](n8n/import-manifest.json) | 15 workflow JSON (`full_clean_import_set`), bindings, credentials |
| [`n8n/data-tables/`](n8n/data-tables/) | CSV lean CAS (`engineering_orchestrator_tasks_v1`) + trace |
| [`docs/architecture/petroleum-mas-research-and-roadmap.md`](docs/architecture/petroleum-mas-research-and-roadmap.md) | архитектура, словарь SCHEDULE keywords, INCLUDE path policy |
| [`docs/architecture/production-readiness-review-2026-08-16.md`](docs/architecture/production-readiness-review-2026-08-16.md) | снимок production-readiness (compose + тесты + residual risks) |
| [`excel-agent-tools/`](excel-agent-tools/) | FastAPI Excel tools + pytest |
| [`fastapi-math-service/`](fastapi-math-service/) | geometry FastAPI |
| [`mas-activity-service/`](mas-activity-service/) | live handoff chat UI (Compose + FastAPI) |
| [`scripts/mas_stack_health.py`](scripts/mas_stack_health.py) | хост-пинг compose-сервисов |
| [`simulation-model-example/combat-dates-revise/`](simulation-model-example/combat-dates-revise/) | commissioning combat cases 0–3 |

## Быстрая проверка репозитория

```bash
export WORKSPACE_ROOT="$PWD"
for f in n8n/tests/*-smoke.js; do node "$f" || exit 1; done
# ~192 scenarios across 17 smoke files

cd mas-activity-service && PYTHONPATH=. python3 -m pytest -q   # 19 passed
cd ../excel-agent-tools && python -m pytest tests               # 70 passed

python3 scripts/mas_stack_health.py
python3 simulation-model-example/combat-dates-revise/run_integration_cases.py
```

Clean import: `n8nio/n8n:2.30.8`, 15 JSON из `full_clean_import_set`, `active=0`.

Compose: `postgres`, `excel-tools`, `n8n`, `n8n-runners`, `mas-activity` (`127.0.0.1:8200`).

Task state — lean CAS (15 колонок); timeline только в `mas_trace_events_v1`.
