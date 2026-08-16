# FastAPI Excel tools

Детерминированный сервис без LLM: принимает Excel, держит дисковую сессию и отдаёт tools для schema/query/extract/validate/export.  
**На работе:** только Windows CMD ниже. Полный MAS — [`docs.md`](../docs.md) §3 (n8n — только UI-импорт).

Каждый запрос к `/api/v1/*` требует `X-API-Key`; исключение — `/health`.

## Windows CMD (канон)

Нужен 64-bit Python 3.11–3.13.

```bat
cd excel-agent-tools
setup-windows.bat
copy excel-tools.env.example excel-tools.env
notepad excel-tools.env
start-windows.bat
```

Проверка во втором CMD: `check-windows.bat`.

Локальный URL для n8n на том же ПК: `http://127.0.0.1:8000/api/v1`. Для корпоративного n8n на другом хосте: `EXCEL_TOOLS_HOST=0.0.0.0` и в Agent Runtime — `http://<IP-Windows>:8000/api/v1` (тот же `API_KEY`).

## Переменные (`excel-tools.env`)

| Переменная | Назначение |
|---|---|
| `API_KEY` | общий секрет FastAPI и Runtime configuration в n8n |
| `EXCEL_TOOLS_HOST`, `EXCEL_TOOLS_PORT` | адрес прослушивания (.bat) |
| `SESSION_DIR`, `SESSION_TTL_HOURS` | каталог и TTL сессий |
| `MAX_FILE_SIZE_MB` | лимит upload |
| `MAX_EXCEL_ZIP_ENTRIES`, `MAX_EXCEL_UNCOMPRESSED_MB` | защита от zip-bomb |
| `MAX_INTERNAL_BLANK_ROWS` | сшивка коротких пустых разрывов внутри таблицы |
| `MAX_PREVIEW_ROWS`, `MAX_QUERY_PREVIEW_ROWS` | лимиты preview |
| `EXCEL_TOOLS_ENABLE_DOCS` | `/docs`; в production оставьте `false` |

Формат файла: `ИМЯ=значение` без `set`, кавычек и пробелов вокруг `=`.

## Docker / тесты (лаборатория)

Сервис входит в корневой `docker-compose.yml` (env из корневого `.env`, не из `excel-tools.env`) — не полевой канон. Тесты: `python -m pytest tests`.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```
