# n8n package

- Workflows: [`workflows/`](workflows/) — `core/` runtime + `support/` templates/reference.
- Import contract: [`import-manifest.json`](import-manifest.json) (`full_clean_import_set` = 17 JSON, target **2.30.8**).
- Activity hydrate (UI import): `Activity — List Tasks` / `Activity — Load Feed` → bind Data Tables → set `ACTIVITY_LIST_URL` / `ACTIVITY_FEED_URL` on mas-activity.
- Data Tables CSV: [`data-tables/`](data-tables/) — lean `engineering_orchestrator_tasks_v1` + `mas_trace_events_v1`.
- Generators: [`templates/`](templates/).
- Code smokes: [`tests/`](tests/) (~187 scenarios).
- SCHEDULE multi-file upload: form field `schedule_files` (drag-and-drop several `.inc/.data/.grdecl`) + optional `schedule_root`; see `templates/schedule_package_materialize.py`.
- Presentation UI: [`../mas-activity-service/`](../mas-activity-service/) (brief / absolute time / specialist duration).
- RAG sheet: [`rag/excel-agent-operating-guide.documents.json`](rag/excel-agent-operating-guide.documents.json).

UI runbook: [`../docs.md`](../docs.md).
