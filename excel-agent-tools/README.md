# FastAPI Excel tools

Это отдельный сервис без LLM, n8n, PostgreSQL и RAG. Он принимает Excel, создаёт дисковую сессию и предоставляет детерминированные инструменты для анализа, фильтрации, проверки и CSV-экспорта.

Данные сессий находятся только в `SESSION_DIR`. Каждый запрос к `/api/v1/*` защищён заголовком `X-API-Key`; исключение — `/health`.

## Windows: только CMD и .bat

Требуется 64-bit Python 3.11, 3.12 или 3.13.

```bat
cd excel-agent-tools
setup-windows.bat
copy excel-tools.env.example excel-tools.env
notepad excel-tools.env
start-windows.bat
```

`setup-windows.bat` создаёт `.venv` и устанавливает зависимости. Активация virtualenv не нужна. Приложение автоматически читает `excel-tools.env` рядом с этим README, а для совместимости — старый `.env`, если нужного значения нет в первом файле. Переменные, уже заданные процессом или Docker, имеют наивысший приоритет. `start-windows.bat` использует тот же механизм и работает в текущем окне до `Ctrl+C`.

Файл `excel-tools.env` использует простой формат `ИМЯ=значение`: без `set`, кавычек и пробелов вокруг `=`. Символ `#` допустим только в начале строки-комментария.

После установки зависимостей сервис можно запустить и без `.bat`:

```bat
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

При таком прямом запуске `excel-tools.env`/`.env` также загружается самим приложением.

Проверка из второго CMD:

```bat
cd excel-agent-tools
check-windows.bat
```

Локальный адрес для n8n на том же ПК: `http://127.0.0.1:8000/api/v1`.

Для n8n на другом сервере:

1. согласуйте адрес и firewall с IT;
2. задайте `EXCEL_TOOLS_HOST=0.0.0.0`;
3. в n8n укажите `http://<IP-вашего-PC>:8000/api/v1`;
4. разрешите TCP/8000 только от сервера n8n.

Не публикуйте Uvicorn напрямую в интернет. Для постоянного размещения нужны TLS/reverse proxy, service account, резервирование каталога сессий и мониторинг.

## Переменные сервиса

| Переменная | Назначение |
|---|---|
| `API_KEY` | общий секрет FastAPI и Runtime configuration в n8n |
| `EXCEL_TOOLS_HOST`, `EXCEL_TOOLS_PORT` | адрес прослушивания, используются .bat |
| `SESSION_DIR`, `SESSION_TTL_HOURS` | каталог и срок жизни сессий |
| `MAX_FILE_SIZE_MB` | максимальный размер загружаемого файла |
| `MAX_EXCEL_ZIP_ENTRIES`, `MAX_EXCEL_UNCOMPRESSED_MB` | защита от zip-bomb в OOXML |
| `MAX_INTERNAL_BLANK_ROWS` | сколько подряд пустых строк можно сшить внутри одной таблицы |
| `MAX_PREVIEW_ROWS`, `MAX_QUERY_PREVIEW_ROWS` | лимиты preview |
| `EXCEL_TOOLS_ENABLE_DOCS` | включает `/docs`; в production оставьте `false` |

Короткие пустые разрывы обрабатываются на сервере: счётчик применяется к каждому разрыву заново, поэтому таблица с пустыми строками через каждые несколько заполненных строк корректно сшивается. Разрыв длиннее лимита остаётся границей таблиц.

## Docker

Сервис входит в корневой `docker-compose.yml`. Его Docker-конфигурация берётся из корневого `.env`, а не из `excel-tools.env`.

## Тесты

Из каталога `excel-agent-tools`:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

В Windows CMD используйте `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`, затем `.venv\Scripts\python.exe -m pytest tests`.
