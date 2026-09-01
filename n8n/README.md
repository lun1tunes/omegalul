# n8n package

- Workflows: [`workflows/`](workflows/) — `core/` live MAS (8 JSON) + `support/` templates + `retired/` archive (**не импортировать**).
- Import contract: [`import-manifest.json`](import-manifest.json). Поле — `runtime_import_order` (8 файлов). Lab `lab_soft_redeploy.py` ещё тянет `full_clean_import_set` (core + support, 14 JSON). Target **2.30.8**.
- Data Tables CSV: [`data-tables/`](data-tables/) — **retired**. Живой стейт кейсов — Postgres за `MAS — Control Plane Proxy`, не n8n Data Tables.
- Generators: [`templates/`](templates/). Commissioning/group-rebind emit: `schedule_timeline_runtime.py` (Node).
- Code smokes: [`tests/`](tests/).
- SCHEDULE multi-file: form `schedule_files` + optional `schedule_root`; INCLUDE-пути не переписываем (`templates/schedule_package_materialize.py`).
- Presentation UI: [`../mas-activity-service/`](../mas-activity-service/) — вход `POST /cases`, лента `GET /cases/{id}/stream`.
- RAG sheet: [`rag/excel-agent-operating-guide.documents.json`](rag/excel-agent-operating-guide.documents.json) (Activity → База знаний). Оркестратор маршрутизирует по `agent_registry`, не по Retrieval.

UI runbook: [`../docs.md`](../docs.md).
