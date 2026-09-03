# FastAPI Excel tools

Детерминированный сервис без LLM: принимает Excel, держит дисковую сессию и отдаёт tools для schema/query/extract/validate/export.  
**На работе:** только Windows CMD ниже. Полный MAS — [`docs.md`](../docs.md) §2 (n8n — только UI-импорт).

Каждый запрос к `/api/v1/*` и `/agent-tools/*` требует `X-API-Key`; исключение — `/health`.

Живой Agent — Excel Extractor в n8n бьёт в **`excel_tools_url` без `/api/v1`**: `http://<IP>:8000` + пути `/agent-tools/…`. URL задаётся в `MAS — Runtime Config`. Ключ — n8n Header Auth credential **Excel Tools X-API-Key** (header `X-API-Key`), не поле Set. Файлы сервис забирает сам: `GET {activity_base_url}/cases/{id}/artifacts/…`.

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

Локальный URL для n8n на том же ПК: `excel_tools_url=http://127.0.0.1:8000` (не `/api/v1`) в **MAS — Runtime Config**. Для корпоративного n8n на другом хосте: `EXCEL_TOOLS_HOST=0.0.0.0` и в Runtime URLs — `http://<IP-Windows>:8000` (тот же `API_KEY` → credential Header Auth `X-API-Key`). Запасной `ACTIVITY_BASE_URL=http://127.0.0.1:8200` в `excel-tools.env`, если задача не передала `activity_base_url`.

## Переменные (`excel-tools.env`)

| Переменная | Назначение |
|---|---|
| `API_KEY` | общий секрет FastAPI и n8n credential Header Auth `Excel Tools X-API-Key` |
| `ACTIVITY_BASE_URL` | запасной URL Activity, если n8n не передал `activity_base_url` |
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
