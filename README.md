# Excel Extractor

Система состоит из трёх независимых частей. Не смешивайте их настройки:

| Компонент | Назначение | Где настраивается |
|---|---|---|
| `excel-agent-tools/` | FastAPI-сервис: хранит Excel-сессии, находит таблицы, фильтрует и экспортирует результат | `excel-agent-tools/excel-tools.env` |
| `n8n/` | AI Agent, память PostgreSQL, RAG, уточнения и три входа: HTTP, Form, вызов из другого workflow | только в UI n8n 2.30.8 |
| `context-seeder/` | опциональная одноразовая загрузка статической инструкции в PGVector напрямую | `context-seeder/context-seeder.env` |

Корневой `.env` относится **только** к локальному запуску полного Docker Compose-стенда. n8n-workflows не используют Global Variables, `$env` или доступ к файловой системе сервера.

## Быстрый запуск на работе: Windows CMD + n8n UI

1. Установите Python 3.11–3.13 и откройте `cmd.exe`.
2. Подготовьте и запустите FastAPI:

```bat
cd excel-agent-tools
setup-windows.bat
copy excel-tools.env.example excel-tools.env
notepad excel-tools.env
start-windows.bat
```

3. Проверьте сервис из второго окна CMD:

```bat
cd excel-agent-tools
check-windows.bat
```

4. В UI n8n 2.30.8 импортируйте в таком порядке:
   - `n8n/workflows/excel-extraction-agent.workflow.json`;
   - `n8n/workflows/excel-engineering-specialist-adapter.workflow.json`;
   - `n8n/workflows/universal-engineering-orchestrator.workflow.json` — единственный основной orchestrator;
   - `n8n/workflows/excel-rag-ingestion.workflow.json` — одноразовое наполнение RAG.
5. В Universal Orchestrator выберите Excel adapter в **Call Excel Extraction Specialist Adapter**, а в adapter выберите Excel Agent в **Call native Excel Extraction Agent**.
6. В узле **Runtime configuration** Excel Agent укажите адрес FastAPI, его API key и отдельный webhook key.
7. Через UI назначьте Data Table, OpenAI/совместимые chat credentials, embedding credential и PostgreSQL credentials.
8. Не активируйте workflows, пока все `REPLACE_*`, workflow bindings и credentials не настроены (поставляемые JSON уже выключены).
9. В RAG workflow выберите тот же embedding credential и PostgreSQL credential, нажмите **Test workflow** один раз. Затем в Excel Agent используйте ту же таблицу и ту же embedding-модель.
10. Активируйте настроенные workflows и тестируйте входы Universal Orchestrator.

`excel-extraction-form-adapter.workflow.json` нужен только для отдельной формы прямого запуска Excel Agent. `excel-mas-orchestrator.workflow.json` — legacy migration-only и в новую установку не импортируется.

Если n8n запущен на удалённом сервере, `127.0.0.1:8000` для него означает сам сервер n8n, а не ваш ПК. В `excel_tools_url` нужен разрешённый IT адрес Windows-машины, например `http://10.20.30.40:8000/api/v1`. Для такого режима задайте `EXCEL_TOOLS_HOST=0.0.0.0` и откройте порт только для адреса n8n.

Подробности:

- [FastAPI tools и Windows CMD](excel-agent-tools/README.md)
- [Импорт и настройка n8n 2.30.8](n8n/README.md)
- [Что такое context-seeder](context-seeder/README.md)

## Docker Compose

Для локального полного стенда:

```bash
cp .env.example .env
docker compose up --build -d
```

Этот режим поднимает PostgreSQL, n8n 2.30.8, task runners и FastAPI tools. `context-seeder` в runtime не входит: для RAG используйте UI-workflow либо запускайте seeder отдельно.

## Проверки репозитория

```bash
cd excel-agent-tools
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
cd ..
python3 -m json.tool n8n/workflows/excel-extraction-agent.workflow.json > /dev/null
python3 -m json.tool n8n/workflows/excel-rag-ingestion.workflow.json > /dev/null
```

На Windows после `setup-windows.bat` запускайте тесты из CMD так: `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`, затем `.venv\Scripts\python.exe -m pytest tests`.

Live-проверка корпоративных credentials, сетевой доступности Windows-хоста и прав PostgreSQL выполняется уже в целевой инфраструктуре.
