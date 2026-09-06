# Retired generators (не живой контур)

Здесь лежат генераторы и контракты **retired**-контура Universal Engineering Orchestrator
(`specialist_packet` / `specialist_result` / `specialist_registry`, CAS persist, Error Handler,
Activity hydrate). Они **не участвуют** в живом импорте (`runtime_import_order` в
`n8n/import-manifest.json`) и не должны меняться при развитии MAS.

Живой контур: `n8n/templates/generate_mas_*.py`, `generate_excel_extractor_agent.py`,
`generate_schedule_builder_agent.py`, `generate_schedule_workflows.py` (только Knowledge
Ingestion / Hybrid Retrieval в `core/`), реестр агентов — таблица `agent_registry` в Postgres
(через Control Plane Proxy). Контракт агентов — `agent_task` → `agent_result`.

Что здесь и зачем оставлено:

| Файл | Выход | Зачем хранить |
|---|---|---|
| `generate_universal_engineering_workflows.py` | `workflows/retired/universal-engineering-orchestrator`, `cas-persist-task`, `excel-extraction-agent`, `tnavigator-schedule-builder` (патч), `workflows/support/*` | заморозка retired JSON; `engineering-specialist-template` до появления шаблона под `agent_task` (Фаза 4 плана) |
| `generate_mas_error_handler.py` | `workflows/retired/mas-error-handler` | стампит `errorWorkflow` только в `retired/` и `support/`; живой обработчик — `Error — MAS Traces` |
| `generate_activity_hydrate_workflows.py` | `workflows/retired/mas-activity-*` | legacy `/v1/hydrate` Activity |
| `mas_handoff_contracts.py`, `*.template.md`, `*.schema.json`, `engineering-task-instruction.template.json` | — | контракты retired-оркестратора |
| `apply_mas_hybrid_rag.py` | одноразовый патчер hybrid-RAG | история миграции |
| `../../contracts/retired/specialist_registry.v1.json` | — | реестр retired-специалистов |

Запуск (только чтобы перегенерировать retired JSON): `python3 n8n/templates/retired/<generator>.py`.
Дымовые тесты retired-контура — `n8n/tests/retired/*.js` (не входят в основной гейт `n8n/tests/*-smoke.js`).
