# Context seeder

`context-seeder` — опциональная одноразовая административная утилита. Она берёт статическую инструкцию AI Agent из `n8n/rag/excel-agent-operating-guide.documents.json`, получает embeddings и записывает их прямо в PGVector.

Она **не** является частью FastAPI, не запускается постоянно, не читает Excel и не обращается к сессиям. Пишет только в PostgreSQL-таблицу `RAG_TABLE_NAME`; по умолчанию это `n8n_excel_agent_context`.

## Что использовать на работе

Если есть только UI n8n, этот Python-seeder не нужен. Импортируйте `n8n/workflows/excel-rag-ingestion.workflow.json`, выберите корпоративные credentials в UI и нажмите **Test workflow** один раз. Это основной рабочий сценарий.

Seeder нужен только если администратор выдал прямой сетевой доступ к PostgreSQL и отдельный embedding API key.

## Windows CMD

```bat
cd context-seeder
setup-windows.bat
copy context-seeder.env.example context-seeder.env
notepad context-seeder.env
run-windows.bat
```

`context-seeder.env` — простой CMD-совместимый файл `ИМЯ=значение`, без `set`, кавычек и пробелов вокруг `=`. При UI-only доступе seeder не запускайте и используйте RAG workflow.

При `REPLACE_EXISTING_CONTEXT=true` утилита в одной транзакции удаляет только старые строки с `metadata.source=excel-agent-operating-guide` и загружает актуальный набор. Другие RAG-данные она не трогает.

Важные условия:

- у PostgreSQL-пользователя должны быть права на таблицу и расширения `vector`/`pgcrypto`;
- `EMBEDDING_DIMENSIONS` должна совпадать с реальной размерностью модели;
- seeder проверяет фактический `vector(n)` у существующей таблицы и останавливается до удаления данных при несовпадении размерности;
- таблица, embedding-модель и размерность должны совпадать с основным n8n workflow;
- n8n credentials недоступны Python-процессу — для seeder нужен отдельный API key.

## Docker

Соберите образ из корня репозитория, чтобы в него попал канонический JSON:

```bash
docker build -f context-seeder/Dockerfile -t excel-context-seeder .
docker run --rm --env-file context-seeder/context-seeder.env excel-context-seeder
```

Seeder намеренно не включён в `docker compose up`: случайная повторная индексация не должна быть частью запуска runtime.
