# Petroleum Engineering MAS

**Единственный полевой runbook:** [`docs.md`](docs.md) — пошаговое развёртывание (Windows-сервисы → UI n8n → bindings → Health → activate).

Перенос на работу без git: `python3 scripts/project_pack.py pack` → скопировать `all.txt` + `scripts/project_pack.py` → `unpack` (см. `docs.md` §0).

| Слой (работа) | Как |
|---|---|
| **n8n 2.30.8** | Только UI: Import from File по `runtime_import_order` (8 core JSON). Data Tables для стейта кейсов **не** создаём. |
| **Excel `:8000` / Schedule `:8090` / Math `:8100` / Activity `:8200`** | Windows CMD: `setup-windows.bat` → `start-windows.bat`. Schedule Builder нужен **Node.js**. |
| **Lab soft-redeploy** | `python3 scripts/lab_soft_redeploy.py` (см. `docs.md` §5–§6) |

Машинный контракт имён: [`n8n/import-manifest.json`](n8n/import-manifest.json) (`runtime_import_order` = поле; `full_clean_import_set` = lab + support). Compose / REST-импорт — только лаборатория (§5 в `docs.md`).

```bash
export WORKSPACE_ROOT="$PWD"
for f in n8n/tests/*-smoke.js; do node "$f" || exit 1; done
cd mas-activity-service && PYTHONPATH=. python3 -m pytest -q
cd ../schedule-builder-service && PYTHONPATH=. python3 -m pytest -q
PYTHONPATH=excel-agent-tools python3 -m pytest excel-agent-tools/tests/test_workflow_contracts.py -q
python3 simulation-model-example/golden-cases/run_ui_smoke.py golden_case_1
python3 simulation-model-example/combat-dates-revise/run_integration_cases.py  # локальная папка, в git нет
python3 scripts/test_project_pack.py
```
