# FastAPI Excel tools

Детерминированный сервис без LLM: принимает Excel, держит дисковую сессию и отдаёт tools для schema/query/extract/validate/export.  
**На работе:** только Windows CMD ниже. Полный MAS — [`docs.md`](../docs.md) §2 (n8n — только UI-импорт).

`/agent-tools/*` без ключа. `API_KEY` в `excel-tools.env` — только если сами включите `X-API-Key` (тогда и Header Auth на агенте в n8n). Прямой `/api/v1/*` монтируется только при `EXCEL_LEGACY_API=1`.

Живой Agent — Excel Extractor в n8n бьёт в **`excel_tools_url` без `/api/v1`**: `http://<IP>:8000` + пути `/agent-tools/…`. URL задаётся в `MAS — Runtime Config`. Файлы сервис забирает сам: `GET {activity_base_url}/cases/{id}/artifacts/…`.

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

Локальный URL для n8n на том же ПК: `excel_tools_url=http://127.0.0.1:8000` (не `/api/v1`) в **MAS — Runtime Config**. Для корпоративного n8n на другом хосте: `EXCEL_TOOLS_HOST=0.0.0.0` и в Runtime URLs — `http://<IP-Windows>:8000`. Запасной `ACTIVITY_BASE_URL=http://127.0.0.1:8200` в `excel-tools.env`, если задача не передала `activity_base_url`.

## Переменные (`excel-tools.env`)

| Переменная | Назначение |
|---|---|
| `API_KEY` | пусто = без авторизации (канон). Если задан — FastAPI ждёт заголовок `X-API-Key` |
| `ACTIVITY_BASE_URL` | запасной URL Activity, если n8n не передал `activity_base_url` |
| `EXCEL_TOOLS_HOST`, `EXCEL_TOOLS_PORT` | адрес прослушивания (.bat) |
| `SESSION_DIR`, `SESSION_TTL_HOURS` | каталог и TTL сессий |
| `MAX_FILE_SIZE_MB` | лимит upload |
| `MAX_EXCEL_ZIP_ENTRIES`, `MAX_EXCEL_UNCOMPRESSED_MB` | защита от zip-bomb |
| `MAX_INTERNAL_BLANK_ROWS` | сшивка коротких пустых разрывов внутри таблицы |
| `MAX_PREVIEW_ROWS`, `MAX_QUERY_PREVIEW_ROWS` | лимиты preview |
| `EXCEL_TOOLS_ENABLE_DOCS` | `/docs`; в production оставьте `false` |
| `EXCEL_LEGACY_API` | `1`/`true`/`yes` — смонтировать `/api/v1` (pytest, ручная отладка книги). Поле и lab по умолчанию без этого флага |

Формат файла: `ИМЯ=значение` без `set`, кавычек и пробелов вокруг `=`.

## Docker / тесты (лаборатория)

Сервис входит в корневой `docker-compose.yml` (env из корневого `.env`, не из `excel-tools.env`) — не полевой канон. Тесты: `python -m pytest tests`.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests
```
