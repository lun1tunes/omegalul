# Petroleum Engineering MAS

Канонический UI-runbook и схема runtime: **[`docs.md`](docs.md)**.

| Артефакт | Назначение |
|---|---|
| [`docs.md`](docs.md) | импорт n8n 2.30.8, Data Tables, bindings, Health Check, smoke |
| [`n8n/import-manifest.json`](n8n/import-manifest.json) | 15 workflow JSON (`full_clean_import_set`), bindings, credentials |
| [`n8n/data-tables/`](n8n/data-tables/) | CSV lean CAS (`engineering_orchestrator_tasks_v1`) + trace |
| [`docs/architecture/petroleum-mas-research-and-roadmap.md`](docs/architecture/petroleum-mas-research-and-roadmap.md) | архитектура, словарь SCHEDULE keywords, INCLUDE path policy |
| [`excel-agent-tools/`](excel-agent-tools/) | FastAPI Excel tools + pytest |
| [`fastapi-math-service/`](fastapi-math-service/) | geometry FastAPI |

## Быстрая проверка репозитория

```bash
WORKSPACE_ROOT="$PWD" node n8n/tests/cas-persist-runtime-smoke.js
# …все n8n/tests/*.js → 160 scenarios

cd excel-agent-tools && python -m pytest tests   # 68 passed
```

Clean import: `n8nio/n8n:2.30.8`, 15 JSON из `full_clean_import_set`, `active=0`.

Task state — lean CAS (15 колонок); timeline только в `mas_trace_events_v1`.
