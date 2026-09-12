# n8n package

- Workflows: [`workflows/`](workflows/) — `core/` live MAS (9 JSON) + `support/demo-agent.workflow.json` (lab / рецепт нового агента).
- Import contract: [`import-manifest.json`](import-manifest.json). Поле — `runtime_import_order` (9 файлов). Lab `lab_soft_redeploy.py` ещё тянет `full_clean_import_set` (core + demo-agent, 10 JSON). Target **2.30.8**.
- Generators: [`templates/`](templates/). JS timeline (`schedule_timeline_runtime.py`) — n8n smokes only; live Schedule Builder emit is Python FastAPI.
- Code smokes: [`tests/`](tests/).
- Presentation UI: [`../mas-activity-service/`](../mas-activity-service/) — вход `POST /cases`, лента `GET /cases/{id}/stream`, лог `GET /cases/{id}/log`.
- RAG sheet: [`rag/excel-agent-operating-guide.documents.json`](rag/excel-agent-operating-guide.documents.json) (Activity → База знаний). Оркестратор маршрутизирует по `agent_registry`, не по Retrieval.

UI runbook: [`../docs.md`](../docs.md).
