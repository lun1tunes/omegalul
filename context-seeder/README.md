# Context seeder

Опциональная одноразовая утилита: читает `n8n/rag/excel-agent-operating-guide.documents.json`, получает embeddings и пишет в PGVector (`RAG_TABLE_NAME`, по умолчанию `n8n_excel_agent_context`).

Для UI-only установки **не нужна**. Используйте `n8n/workflows/excel-rag-ingestion.workflow.json` → **Test workflow** (не Publish). После insert смотрите `Summarize RAG inventory`. Общий guide — корневой [`README.md`](../README.md).

## Когда нужен seeder

Только при прямом доступе к PostgreSQL и отдельном embedding API key.

```bat
cd context-seeder
setup-windows.bat
copy context-seeder.env.example context-seeder.env
notepad context-seeder.env
run-windows.bat
```

При `REPLACE_EXISTING_CONTEXT=true` удаляются только строки с `metadata.source=excel-agent-operating-guide`. Модель/размерность должны совпадать с Excel Agent PGVector nodes.
