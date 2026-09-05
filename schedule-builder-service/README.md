# Schedule Builder FastAPI

Parse / apply / emit файлов `SCHEDULE` (`.inc` / `.data`). LLM в n8n **не** пишет `.INC`.

**На работе:** Windows CMD, Python 3.11–3.13, `.venv`. Docker и Node.js **не нужны**. Полный порядок — [`../docs.md`](../docs.md) §2.

Commissioning и group-rebind — Python (`app/timeline_ops.py`).

## Windows CMD

```bat
cd schedule-builder-service
setup-windows.bat
copy schedule-builder.env.example schedule-builder.env
notepad schedule-builder.env
start-windows.bat
```

Проверка: `check-windows.bat`.

`ACTIVITY_BASE_URL=http://127.0.0.1:8200` (или IP этого ПК). Файлы: `GET {activity}/cases/{id}/artifacts/{id}`. INCLUDE-пути не переписываются; Petrel `../../INCLUDE/…` оставляют `KEEP`, если тело не приложено.

Порт канона: **8090**. URL в n8n задаётся один раз в `MAS — Runtime Config` (`schedule_service_url`, `activity_base_url`).

## Каталог keyword (без класса на DATES/WCONPROD)

Расклад полей живёт в RAG `schema_catalogue` (`schedule_mvp`). FastAPI на старте читает тот же JSON (или `app/data/schema_catalogues.json` на поле без n8n/rag). Агент вызывает `get_keyword` и получает `details.parameters` (позиция, тип, unit, описание). Рендер — `POST /render` / tool `render_ir`: IR `fields` → строка `.inc` по `layout` (record `/`, затем голый `/` блока и пустая строка).

Новый keyword = карточка в корпусе + allowlist. Python-класс на каждое слово не добавляем. Обновить снимок: `python3 -m app.schema_store` из каталога сервиса.

