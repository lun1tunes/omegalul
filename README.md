# Petroleum Engineering MAS

Канонический runbook: **[`docs.md`](docs.md)**.

### Полевой контур (работа)

| Слой | Как |
|---|---|
| **n8n 2.30.8** | Только **UI**: Import from File, Data Tables, credentials, bindings |
| **Excel / Math / Activity** | Только **Windows CMD** (`setup-windows.bat` → `start-windows.bat`) |

Compose / REST-импорт workflow — лаборатория, не канон на работе. Подробности и порядок шагов — в [`docs.md`](docs.md) §3.

| Артефакт | Назначение |
|---|---|
| [`docs.md`](docs.md) | UI-импорт, Data Tables, bindings, Health Check, Windows-сервисы |
| [`n8n/import-manifest.json`](n8n/import-manifest.json) | 17 workflow JSON, bindings, credentials |
| [`n8n/data-tables/`](n8n/data-tables/) | CSV lean CAS + trace |
| [`docs/architecture/petroleum-mas-research-and-roadmap.md`](docs/architecture/petroleum-mas-research-and-roadmap.md) | архитектура, keywords, INCLUDE policy |
| [`docs/architecture/production-readiness-review-2026-08-16.md`](docs/architecture/production-readiness-review-2026-08-16.md) | снимок readiness |
| [`excel-agent-tools/`](excel-agent-tools/) | FastAPI Excel (Windows `.bat`) |
| [`fastapi-math-service/`](fastapi-math-service/) | geometry FastAPI (Windows `.bat`) |
| [`mas-activity-service/`](mas-activity-service/) | handoff UI (Windows `.bat`) |
| [`scripts/mas_stack_health.py`](scripts/mas_stack_health.py) | пинг лабораторного Compose |
| [`simulation-model-example/combat-dates-revise/`](simulation-model-example/combat-dates-revise/) | commissioning combat 0–3 |

## Быстрая проверка репозитория (лаборатория / CI)

```bash
export WORKSPACE_ROOT="$PWD"
for f in n8n/tests/*-smoke.js; do node "$f" || exit 1; done
cd mas-activity-service && PYTHONPATH=. python3 -m pytest -q
cd ../excel-agent-tools && python -m pytest tests
python3 simulation-model-example/combat-dates-revise/run_integration_cases.py
```

Clean UI import: 17 JSON из `full_clean_import_set`, после импорта `active=0`, затем биндинги → Health Check → activate.
