# Petroleum Engineering MAS

**Единственный runbook:** [`docs.md`](docs.md) — пошаговое развёртывание (Windows-сервисы → UI n8n → bindings → RAG → Health → activate).

Перенос на работу без git: `python3 scripts/project_pack.py pack` → скопировать `all.txt` + `scripts/project_pack.py` → `unpack` (см. `docs.md` §0).

| Слой (работа) | Как |
|---|---|
| **n8n 2.30.8** | Только UI: Import from File, Data Tables, credentials, bindings |
| **Excel / Math / Activity** | Windows CMD: `setup-windows.bat` → `start-windows.bat` |
| **Lab soft-redeploy** | `python3 scripts/lab_soft_redeploy.py` (см. `docs.md` §5–§6) |

Машинный контракт имён: [`n8n/import-manifest.json`](n8n/import-manifest.json). Compose / REST-импорт — только лаборатория (§5 в `docs.md`).

```bash
export WORKSPACE_ROOT="$PWD"
for f in n8n/tests/*-smoke.js; do node "$f" || exit 1; done
cd mas-activity-service && PYTHONPATH=. python3 -m pytest -q
cd ../excel-agent-tools && python -m pytest tests
python3 simulation-model-example/combat-dates-revise/run_integration_cases.py  # локальная папка, в git нет
python3 scripts/test_project_pack.py
```
