# Petroleum Engineering MAS — развёртывание в n8n 2.30.8

Практичный MVP для создания и проверки `SCHEDULE` в режимах `CREATE` и preserve-by-default `REVISE`. Входные `.data/.inc`, Excel, `.dev` и CPS3 обрабатываются внутри n8n и локальных FastAPI-сервисов. Результат возвращается текстом как `schedule.inc`.

**Полный корпоративный UI-гайд с нуля (импорт → таблицы → bindings → Health Check):** [`docs/DEPLOY_N8N_UI.md`](docs/DEPLOY_N8N_UI.md).  
CSV шаблоны Data Tables: [`n8n/data-tables/`](n8n/data-tables/).  
Кнопка проверки связности: `Form — MAS Deployment Health Check`.

Требования: n8n строго `2.30.8`, PostgreSQL с PGVector, OpenAI/OpenAI-compatible credentials и Python 3.11–3.13 на Windows. Все действия ниже выполняются через UI n8n и обычный CMD; Global Variables, `$env`, PowerShell и доступ к серверной файловой системе не нужны.

## 1. Запустить локальные сервисы в Windows CMD

### Excel Tools

```bat
cd excel-agent-tools
setup-windows.bat
copy excel-tools.env.example excel-tools.env
notepad excel-tools.env
start-windows.bat
```

Обязательно задайте в `excel-tools.env` уникальный `API_KEY`. Полезные настройки: `SESSION_DIR`, `SESSION_TTL_HOURS`, `MAX_FILE_SIZE_MB`, `MAX_INTERNAL_BLANK_ROWS`, `MAX_PREVIEW_ROWS`, `MAX_QUERY_PREVIEW_ROWS`.

Проверка во втором CMD:

```bat
cd excel-agent-tools
check-windows.bat
```

Локальный URL: `http://127.0.0.1:8000/api/v1`. Если n8n работает на другом сервере, задайте `EXCEL_TOOLS_HOST=0.0.0.0`, используйте в n8n `http://<IP-Windows-PC>:8000/api/v1` и разрешите TCP/8000 от сервера n8n.

### Math Service

```bat
cd fastapi-math-service
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Проверка: `http://127.0.0.1:8100/health`. Для удалённого n8n запустите с `--host 0.0.0.0`, используйте `http://<IP-Windows-PC>:8100/api/v1/math` и разрешите TCP/8100 от сервера n8n.

Math Service принимает одну ASCII CPS3/ZMAP-поверхность и до 256 `.dev` за один batch. DEV ожидается как `MD X Y Z`; траектории и поверхность должны иметь одинаковые CRS, единицы, datum и направление Z.

## 2. Импортировать runtime workflows

В n8n откройте **Workflows → Import from File** и импортируйте по порядку:

| № | JSON | Название в UI |
|---:|---|---|
| 1 | `n8n/workflows/calculation-specialist-adapter.workflow.json` | `Adapter — Calculation (Math Service)` |
| 2 | `n8n/workflows/excel-extraction-agent.workflow.json` | `Agent — Excel Extractor` |
| 3 | `n8n/workflows/excel-engineering-specialist-adapter.workflow.json` | `Adapter — Excel Extraction` |
| 4 | `n8n/workflows/tnavigator-schedule-knowledge-ingestion.workflow.json` | `SCHEDULE — Knowledge Ingestion` |
| 5 | `n8n/workflows/tnavigator-schedule-hybrid-retrieval.workflow.json` | `SCHEDULE — Knowledge Retrieval` |
| 6 | `n8n/workflows/tnavigator-schedule-builder.workflow.json` | `SCHEDULE — Builder` |
| 7 | `n8n/workflows/mas-trace-event-writer.workflow.json` | `Writer — MAS Trace` |
| 8 | `n8n/workflows/universal-engineering-orchestrator.workflow.json` | `Orchestrator — Engineering MAS` |
| 9 | `n8n/workflows/mvp-entry-form.workflow.json` | `Form — MAS Entry` |
| 10 | `n8n/workflows/mas-human-gate-form.workflow.json` | `Form — MAS Human Gate` |
| 11 | `n8n/workflows/mas-deployment-health-check.workflow.json` | `Form — MAS Deployment Health Check` |

Все схемы импортируются неактивными. Полный clean-import набор — **16** JSON из [`n8n/import-manifest.json`](n8n/import-manifest.json) (`full_clean_import_set`); он совпадает с файлами в `n8n/workflows/`. Для обычного запуска сверх таблицы выше остальные JSON не нужны: `Legacy — Excel Orchestrator` не активировать; `Ingestion — Excel Agent Knowledge`, `Template — Engineering Specialist` и `Reference — AI Components` — опциональны. Точные click-path для таблиц и Call-нод — в [`docs/DEPLOY_N8N_UI.md`](docs/DEPLOY_N8N_UI.md).

SCHEDULE runtime — только Ingestion, Retrieval и Builder. Отдельных diagnostic `tnavigator-schedule-intake|baseline-*|planner|renderer|merge|validator|verifier|release` больше нет: стадии живут Code-нодами внутри Builder, accountable release — в Orchestrator (`Apply action and version guard`).

## 3. Создать две Data Tables

В UI n8n создайте таблицу `engineering_orchestrator_tasks_v1`:

| Колонка | Тип | Колонка | Тип |
|---|---|---|---|
| `task_id` | String | `version` | Number |
| `status` | String | `phase` | String |
| `task_type` | String | `risk_class` | String |
| `request_json` | String | `context_json` | String |
| `plan_json` | String | `specialist_json` | String |
| `result_json` | String | `verification_json` | String |
| `pending_human_json` | String | `last_error_json` | String |
| `retry_count` | Number | `max_retries` | Number |
| `history_json` | String | `created_at` | String |
| `updated_at` | String |  |  |

В workflow `Orchestrator — Engineering MAS` выберите эту таблицу в каждой ноде:

- `Insert durable task state`;
- `Load task by ID`;
- `CAS persist human action then plan`;
- `CAS persist terminal human action`;
- `CAS persist plan or human gate`;
- `CAS persist SCHEDULE evidence retry`;
- `CAS persist SCHEDULE resume`;
- `CAS persist verification`;
- `CAS persist specialist gate or error`;
- `CAS persist routing gate`.

Создайте таблицу `mas_trace_events_v1`:

| Колонка | Тип | Колонка | Тип |
|---|---|---|---|
| `event_id` | String | `trace_id` | String |
| `task_id` | String | `at` | String |
| `stage` | String | `event_type` | String |
| `actor` | String | `status` | String |
| `summary` | String | `details_json` | String |

В workflow `Writer — MAS Trace`, нода `Insert MAS trace event`, выберите `mas_trace_events_v1`.

## 4. Связать workflows

Откройте каждую указанную Execute Workflow node и выберите target из списка:

| Workflow | Нода | Выбрать workflow |
|---|---|---|
| `Orchestrator — Engineering MAS` | `Call Excel Extraction Specialist Adapter` | `Adapter — Excel Extraction` |
| `Orchestrator — Engineering MAS` | `Call SCHEDULE Hybrid Retrieval` | `SCHEDULE — Knowledge Retrieval` |
| `Orchestrator — Engineering MAS` | `Call SCHEDULE Builder Specialist` | `SCHEDULE — Builder` |
| `Orchestrator — Engineering MAS` | `Call Calculation Specialist` | `Adapter — Calculation (Math Service)` |
| `Orchestrator — Engineering MAS` | `Call MAS Trace Event Writer` | `Writer — MAS Trace` |
| `Adapter — Excel Extraction` | `Call native Excel Extraction Agent` | `Agent — Excel Extractor` |
| `Form — MAS Entry` | `Call Universal Engineering Orchestrator` | `Orchestrator — Engineering MAS` |
| `Form — MAS Human Gate` | `Call Orchestrator status` | `Orchestrator — Engineering MAS` |
| `Form — MAS Human Gate` | `Call Orchestrator resume` | `Orchestrator — Engineering MAS` |
| `Form — MAS Deployment Health Check` | `Call Orchestrator probe` | `Orchestrator — Engineering MAS` |
| `Form — MAS Deployment Health Check` | `Call Trace Writer probe` | `Writer — MAS Trace` |

`Call Data Specialist` и `Call Document Specialist` пока не настраиваются: это точки расширения. Полный чеклист Data Table нод и Health Check — [`docs/DEPLOY_N8N_UI.md`](docs/DEPLOY_N8N_UI.md).

## 5. Назначить credentials и адреса

### Orchestrator — Engineering MAS

- `Planner Chat Model — configure in UI` → дешёвая chat model;
- `Verifier Chat Model — separate credential` → chat model Verifier;
- `Authenticated engineering webhook` → Header Auth только если нужен HTTP-вход. Для основной формы не требуется.

### SCHEDULE — Builder

- `SCHEDULE Planner Chat Model — configure in UI` → chat model;
- `SCHEDULE Builder Chat Model — configure in UI` → chat model.

### Agent — Excel Extractor

- `Runtime configuration`: заполнить `excel_tools_url`, `excel_tools_api_key`, `excel_webhook_api_key`;
- `OpenAI Chat Model — gpt-4.1-nano` → дешёвая chat model;
- `Postgres Chat Memory — session scoped` → PostgreSQL credential;
- `PGVector operating context` → PostgreSQL credential;
- `OpenAI Embeddings — text-embedding-3-small` → embedding credential.

### Adapter — Calculation (Math Service)

- `Math Service Configuration` → `math_service_url`; локально `http://127.0.0.1:8100/api/v1/math`, для удалённого n8n — адрес Windows-PC.

### SCHEDULE — Knowledge Ingestion

Назначьте PostgreSQL credential в:

- `PGVector — insert approved SCHEDULE knowledge`;
- `Finalize indexes and deduplicate chunks`;
- `PostgreSQL — upsert full parent knowledge`;
- `PostgreSQL — upsert approved schema catalogue`.

В `SCHEDULE Embeddings — configure same model in retrieval` назначьте embedding credential.

### SCHEDULE — Knowledge Retrieval

Назначьте PostgreSQL credential в:

- `PostgreSQL lexical + exact candidates`;
- `PostgreSQL tag candidates`;
- `PGVector semantic candidates`;
- `PostgreSQL full parent knowledge`;
- `PostgreSQL approved schema catalogue`.

В `SCHEDULE Retrieval Embeddings — same model as ingestion` назначьте тот же embedding credential. Модель и размерность ingestion/retrieval должны совпадать; текущая конфигурация — `text-embedding-3-small`, 1536.

## 6. Наполнить SCHEDULE RAG

Откройте `SCHEDULE — Knowledge Ingestion`, заполните встроенную форму и выполните workflow. Один запуск загружает один экспертный блок типа `keyword_instruction` или `worked_example` в `target_base=schedule_mvp`.

Эквивалентный контракт для Execute Workflow:

```json
{
  "schedule_knowledge_block": {
    "contract": "schedule_knowledge_block",
    "contract_version": "1.0",
    "target_base": "schedule_mvp",
    "knowledge_type": "keyword_instruction",
    "knowledge_id": "wconprod-forecast-v1",
    "revision": "1",
    "title": "WCONPROD — прогнозный контроль скважины",
    "keywords": ["WCONPROD"],
    "topics": ["Контроль по скважинам", "Прогноз"],
    "task_patterns": ["задать ограничение по воде"],
    "status": "active",
    "author": "expert-name",
    "access_scope": "petroleum-engineering",
    "text": "Полная экспертная инструкция, ограничения и пример..."
  }
}
```

Для deterministic render добавляйте `schema_catalogue_json` с точным порядком полей, типами, обязательностью, defaults и enums. Retrieval совмещает lexical PostgreSQL, semantic PGVector, exact tags и RRF. При отсутствии активной инструкции система останавливается и задаёт HITL-вопрос.

Excel operating guide при необходимости загружается однократным запуском `Ingestion — Excel Agent Knowledge` (`n8n/workflows/excel-rag-ingestion.workflow.json`). Workflow не публикуйте: достаточно Test workflow. После insert нода `Summarize RAG inventory` показывает содержимое таблицы из UI (без доступа к серверу). `context-seeder` для UI-only установки не нужен.

## 7. Запустить MAS

1. Сохраните все настроенные workflows.
2. Сначала активируйте и прогоните `Form — MAS Deployment Health Check` — исправьте каждый **FAIL** по колонке `where_to_fix`.
3. Опубликуйте/активируйте `Form — MAS Entry` и `Form — MAS Human Gate`.
4. Откройте Production Form URL Entry.
5. Заполните `Task Description`; приложите Excel, `.data/.inc/.sch`, `.dev` и CPS3 по задаче.
6. На completion-странице получите либо `schedule.inc`, либо понятный HITL-блок: что не так, вопросы оркестратора и `task_id`.

Для продолжения откройте `Form — MAS Human Gate`, вставьте `task_id`, выберите `reply` / `approve` / `reject` и ответьте текстом. Форма сама делает `status`, подставляет `expected_version` и `gate_id` и вызывает Orchestrator. Ручное копирование CAS-полей не нужно. Неверная версия или gate по-прежнему завершаются conflict без перезаписи состояния.

## 8. Smoke-check перед работой

- `CREATE` без baseline возвращает синтаксически проверенный `schedule.inc`;
- `REVISE` с `.data/.inc` сохраняет незатронутые блоки и показывает diff;
- Excel evidence gap вызывает Extractor, сохраняет состояние и возвращается в Builder;
- `.dev` + CPS3 дают batch-результаты `filename/intersection_md/x/y/z`;
- отсутствие данных создаёт понятный HITL-вопрос;
- stale `expected_version` и неверный `gate_id` не изменяют задачу;
- в `mas_trace_events_v1` нет секретов, binary и raw prompt payloads.

Если появляется `toolHttpRequest has a supplyData method but no execute method`, удалите старую копию `Agent — Excel Extractor`, заново импортируйте актуальный JSON и перепривяжите Excel Adapter. Tool HTTP Request нельзя запускать отдельно: все семь tool-нод должны иметь только связь `Tool → Excel Extractor AI Agent` типа `ai_tool`, без `main`-связей.

## 9. Проверка репозитория

Из корня репозитория:

```bash
WORKSPACE_ROOT="$PWD" node n8n/tests/schedule-intake-runtime-smoke.js
# …и остальные n8n/tests/*.js (всего 121 scenario)

cd excel-agent-tools
python -m pip install -r requirements-dev.txt
python -m pytest tests
```

Ожидание: все smoke зелёные; pytest `excel-agent-tools/tests` зелёный. Clean import официального `n8nio/n8n:2.30.8` должен принять все 16 JSON, после импорта `active=0`. Подробности по пакетам: [`n8n/README.md`](n8n/README.md), [`excel-agent-tools/README.md`](excel-agent-tools/README.md), [`fastapi-math-service/README.md`](fastapi-math-service/README.md), [`context-seeder/README.md`](context-seeder/README.md).

## Опциональный локальный Docker

```bash
cp .env.example .env
docker compose up --build -d
```

Compose поднимает n8n `2.30.8`, PostgreSQL/PGVector и Excel Tools. На рабочей Windows Docker для FastAPI не требуется.

## Репозиторий

- `n8n/` — 16 workflow JSON, import-manifest, data-tables CSV, генераторы и smoke-тесты;
- `docs/DEPLOY_N8N_UI.md` — корпоративный UI deploy с нуля + Health Check;
- `excel-agent-tools/` — Excel FastAPI (см. свой README);
- `fastapi-math-service/` — NumPy geometry FastAPI (см. свой README);
- `context-seeder/` — опциональный прямой seeder; для UI-only не нужен;
- `postgres-init/` — локальная инициализация PostgreSQL/PGVector;
- `docs/architecture/` — архитектура и roadmap (`MAS_ARCHITECTURE.md`, petroleum roadmap).

Секреты не должны попадать в JSON, Data Tables или git. В MVP отсутствуют Artifact Store и автоматический tNavigator runner: SCHEDULE остаётся обычным ограниченным текстом внутри n8n.
