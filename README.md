# Petroleum Engineering MAS

**Полевой гайд:** [`docs.md`](docs.md) — схема, развёртывание на Windows + корпоративный n8n, как пользоваться.

Перенос на работу без git: `python3 scripts/project_pack.py pack` → скопировать `all.txt` + `scripts/project_pack.py` → `unpack` (см. `docs.md`).

| Слой (работа) | Как |
|---|---|
| **n8n 2.30.8** | Только UI: Import from File по `IMPORT_ORDER.txt` (9 JSON). Data Tables для стейта кейсов не создаём. |
| **Excel `:8000` / Schedule `:8090` / Math `:8100` / Activity `:8200`** | Windows CMD: `setup-windows.bat` → `start-windows.bat`. Только Python `.venv`, без Docker и без Node. |
| **Lab soft-redeploy** | `python3 scripts/lab_soft_redeploy.py` (`--hot` если lab уже поднят) |
| **Lab gate** | `python3 scripts/mas_gate.py` (offline); `--live` 12 сценариев ~20 мин; `--live --repeat 3` — шесть commissioning ×3 + остальные один раз |

Машинный контракт имён: [`n8n/import-manifest.json`](n8n/import-manifest.json) (`runtime_import_order` = поле; `full_clean_import_set` = lab + support). Compose / REST-импорт — только лаборатория.

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
