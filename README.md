# Excel Extractor Agent — n8n + FastAPI

Production-oriented Excel component for a future MAS. It contains a native n8n AI Agent core, authenticated form/MAS adapters, and a separate UI-only RAG ingestion workflow:

- **n8n 2.30.8** — orchestration, three entry points and the AI Agent;
- **OpenAI `gpt-4.1-nano`, temperature `0`** — only chat model;
- **OpenAI `text-embedding-3-small` (1536 dimensions) by default** — replaceable in UI with the approved corporate embedding model;
- **PostgreSQL 16 + pgvector** — n8n database, session-scoped chat memory and static operating context;
- **FastAPI Excel Tools** — deterministic, authenticated workbook session/tool service; it never calls an LLM;
- **n8n-runners** — separate hardened external execution runner for JavaScript Code nodes.

The delivery workflows contain no Qwen credential or model. The imported Qwen example is not part of this component. For the corporate installation, chat remains `gpt-4.1-nano`; the embedding subnode is intentionally replaceable in UI.

## Architecture and contracts

The active core workflow is **`Excel Extractor Agent — OpenAI nano + FastAPI tools`** (`1NPVfPP868n5hw7D`). It has all requested components in the same workflow graph:

1. native AI Agent connected to `gpt-4.1-nano` with temperature `0`;
2. PostgreSQL Chat Memory keyed as `excel:<session_id>`;
3. PGVector context-search tool backed by `n8n_excel_agent_context` and `text-embedding-3-small`;
4. exactly one HTTP AI-tool node per FastAPI Excel tool;
5. deterministic preflight/finalization guards around the model.

The model receives compact tool outputs only. Workbooks stay in the FastAPI session store (Docker volume in the reference topology or a local Windows directory). The system prompt requires discovery before querying, exact opaque IDs, verified columns/values, hard-stop clarification on material ambiguity, and no invented data. After `query_table`, a deterministic workflow tail—not the model—validates the result, writes a CSV artifact and finalizes the response.

The available Excel tools are:

`workbook_introspect`, `sheet_preview`, `detect_tables`, `describe_table`, `list_column_values`, `query_table`, `validate_result`, `export_result`, `get_session_state`, `save_agent_plan`, `submit_clarification`, `resolve_clarification`, `finalize_extraction`.

Tool calls for the same session are serialized by `filelock` using the operating system’s locking mechanism. Session state is atomically saved. Exact retries of a resolved clarification are idempotent; conflicting answers are rejected.

### Пустые строки внутри таблиц

`detect_tables` потоково сшивает **до 5 подряд полностью пустых строк в каждом разрыве** внутри уже начатой таблицы; после очередной строки данных счётчик сбрасывается. Поэтому последовательность «данные → пустые строки → данные → пустые строки → данные» остаётся одной таблицей, если каждый непрерывный разрыв укладывается в лимит. Пустые строки не становятся записями: `describe_table`, `query_table` и экспорт возвращают только фактические данные. Лимит задаётся `MAX_INTERNAL_BLANK_ROWS` (0–100; по умолчанию `5`) и намеренно ограничен: более длинный разрыв завершает текущую таблицу, а строка, похожая на новый заголовок с последующей записью, открывает новый блок. Это предотвращает неявное склеивание независимых таблиц и сохраняет память детектора O(ширина таблицы), а не O(число строк листа).

## Entry points

All routes normalize to the same core workflow and structured result (`success`, `partial`, `clarification_needed`, or `error`).

- **HTTP:** `POST /webhook/excel-extract`, protected by `X-Excel-Webhook-Key`.
- **Form:** `/form/excel-extract-form`, protected by n8n user authentication. The form adapter calls the core via Execute Sub-workflow.
- **Another n8n workflow:** use **Execute Sub-workflow** → core workflow. Pass `binary.file` and `request`; for a continuation pass `session_id` and `clarification_response` with no file.
- **MAS orchestration:** `POST /webhook/excel-mas-orchestrator`, protected by its own `X-Excel-MAS-Key`; it delegates to the core via Execute Sub-workflow and uses PostgreSQL memory only for the Russian-facing orchestration message.

For a clarification, retain the returned `meta.session_id`; send all answers with the returned `clarification.token`. The original workbook is not uploaded again. The workflow resolves the answer deterministically, then the agent reads the same session state and resumes.

## Local testing on Windows

For local development on Windows, `uvicorn` can be run natively. The `locked_session` method uses `msvcrt` on Windows and `fcntl` on Linux. The default `SESSION_DIR` fallback on Windows uses the system temporary directory.

### Бесплатный n8n 2.30.8: настройка только через UI

Импортируемые workflow JSON **не используют** Global Variables, `$vars` или `$env` n8n. После импорта откройте основной workflow **Excel Extractor Agent — OpenAI nano + FastAPI tools** и измените только узел **Runtime configuration**:

1. `excel_tools_url` — базовый адрес FastAPI, обязательно с `/api/v1` в конце;
2. `excel_tools_api_key` — значение заголовка `X-API-Key` FastAPI;
3. `excel_webhook_api_key` — ключ заголовка `X-Excel-Webhook-Key` для HTTP-входа.

Затем в UI назначьте/пересоздайте обычные n8n credentials для OpenAI и PostgreSQL. Для корпоративного RAG замените **оба** embedding-subnode (в core и в ingestion workflow) на один и тот же разрешённый embedding model. Никакой доступ к shell, файловой системе сервера или его переменным окружения не нужен.

Адрес должен быть доступен **с сервера n8n**. Если корпоративный n8n работает удалённо, `127.0.0.1:8000` указывает на сам сервер n8n, а не на ваш рабочий ноутбук. Для теста с локальным n8n в Docker Desktop подходит `http://host.docker.internal:8000/api/v1`; для удалённого n8n ИТ должны обеспечить маршрут/VPN/firewall rule, защищённый tunnel или внутренний reverse proxy до Windows-хоста с FastAPI.

Значения в узле Runtime configuration — удобный вариант для первичного UI-теста, но они сохраняются в данных workflow. Перед промышленной эксплуатацией ИТ должны перенести ключ FastAPI в credential/secret store и ограничить доступ к workflow.

## Deploy

Docker is mandatory for the complete production topology. The **FastAPI Excel Tools service itself is cross-platform** and can also run natively on Windows without Docker (for local development, a Windows-hosted deployment, or when n8n runs separately). A native process does not provide n8n, PostgreSQL/pgvector, external task runners, Docker volume isolation, or the production network boundary by itself.

```bash
cp .env.example .env
# Replace every change-me value with independently generated secrets.
docker compose --env-file .env config --quiet
docker compose up -d --build
docker compose ps
```

Required secret variables include `POSTGRES_PASSWORD`, `N8N_ENCRYPTION_KEY`, `N8N_USER_MANAGEMENT_JWT_SECRET`, `N8N_RUNNERS_AUTH_TOKEN`, `EXCEL_TOOLS_API_KEY`, `EXCEL_WEBHOOK_API_KEY`, and `OPENAI_API_KEY`. `N8N_RUNNERS_AUTH_TOKEN` is shared only over the private backend network by n8n and `n8n-runners`.

Only n8n binds a host port, and it binds loopback by default. PostgreSQL, FastAPI, and the task broker have no host ports. Production must put n8n behind an authenticated TLS reverse proxy, set `N8N_PROTOCOL=https`, HTTPS `N8N_EDITOR_BASE_URL`/`N8N_WEBHOOK_URL`, and retain `N8N_SECURE_COOKIE=true`. Apply request-size limits and rate limits at that proxy.

### Native FastAPI on Windows (without Docker)

Supported on Windows 10/11 and Windows Server with **Python 3.11–3.13 x64**. In PowerShell:

```powershell
cd excel-agent-tools
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

$env:API_KEY = "replace-with-a-long-random-secret"
$env:SESSION_DIR = "$PWD\data\sessions"
$env:SESSION_TTL_HOURS = "24"
$env:MAX_FILE_SIZE_MB = "200"
$env:MAX_EXCEL_ZIP_ENTRIES = "10000"
$env:MAX_EXCEL_UNCOMPRESSED_MB = "500"
$env:MAX_INTERNAL_BLANK_ROWS = "5"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use `http://127.0.0.1:8000/health` to check the service. Its session lock uses `filelock`, which maps to platform-native locking on Windows and Unix; do not run multiple worker processes against a network share unless that filesystem’s locking semantics have been validated. For a Windows FastAPI process with n8n in Docker Desktop, set `EXCEL_TOOLS_URL=http://host.docker.internal:8000/api/v1` for n8n and allow the port only from the Docker/host boundary (or, preferably, place both services behind a private proxy). Do not expose the raw FastAPI port publicly; it requires `X-API-Key` but should still be behind TLS, firewall and rate limits.

## Импорт и запуск через UI n8n 2.30.8 — без доступа к серверу

Это основной путь для рабочего окружения. Нужны только файлы JSON из репозитория, браузер с UI n8n и учётные данные, созданные в UI. Ни `$env`, ни `$vars`, ни Global Variables, ни shell/SQL/Docker на сервере n8n не используются.

### Один раз: FastAPI на Windows

1. На Windows поднимите `excel-agent-tools` по инструкции выше, проверьте `http://127.0.0.1:8000/health`.
2. Убедитесь, что **сервер n8n** маршрутизируется до Windows FastAPI. Если n8n не на том же компьютере, `127.0.0.1` использовать нельзя: нужен адрес Windows-хоста, VPN/tunnel или внутренний reverse proxy, согласованный с ИТ.
3. Сохраните длинный случайный `API_KEY`: он будет указан и в Windows PowerShell, и в Runtime configuration core workflow.

### Последовательность импорта

1. Импортируйте `n8n/workflows/excel-extraction-agent.workflow.json`.
2. Откройте **Runtime configuration** и задайте `excel_tools_url` (с `/api/v1`), `excel_tools_api_key`, `excel_webhook_api_key`.
3. В UI выберите credentials для **OpenAI Chat Model — gpt-4.1-nano**, **PostgreSQL Chat Memory — session scoped** и **PGVector operating context**. Для core сохраните `temperature: 0`.
4. Для корпоративного embedding удалите placeholder **OpenAI Embeddings — text-embedding-3-small**, добавьте разрешённый корпоративный Embedding Model из UI и соедините его с **PGVector operating context**. Запомните конкретную модель и её размерность.
5. Импортируйте `n8n/workflows/excel-rag-ingestion.workflow.json`. В **RAG runtime configuration** укажите тот же `rag_table_name`, что в core node **PGVector operating context**. Выберите PostgreSQL credential, замените placeholder embedding subnode на **тот же** корпоративный embedding model и соедините его с **PGVector — insert operating guide**. Нажмите **Test workflow** один раз; ingestion workflow активировать не нужно.
6. Импортируйте `n8n/workflows/excel-extraction-form-adapter.workflow.json` и `n8n/workflows/excel-mas-orchestrator.workflow.json` при необходимости. В каждом **Execute Sub-workflow** выберите только что импортированный core workflow. В **MAS runtime configuration** задайте отдельный случайный ключ и вызывайте MAS только с `X-Excel-MAS-Key`.
7. Сохраните и активируйте core; после этого проверьте HTTP, Form и Execute Sub-workflow. MAS активируйте только после настройки ключа.

### Переносимая RAG-база

- Канонический текст: `n8n/rag/excel-agent-operating-guide.documents.json`; это пять коротких документов о границах доверия, discovery/blank gaps, query/result, clarification и эксплуатации RAG.
- Тот же текст встроен в node **RAG documents — portable operating guide**, поэтому для загрузки не нужен доступ к файлам сервера. JSON рядом нужен для ревью/версирования и при желании редактируется до импорта.
- `Default Data Loader` + `Recursive Character Text Splitter (1000/150)` + PGVector insert совместимы с n8n **2.30.8**. Локальная проверка на этой версии успешно создала 10 chunks размерности 1536 с `text-embedding-3-small`.
- Вставка и поиск **обязаны** использовать одинаковую embedding-модель и размерность. На новой таблице PGVector создаёт векторную колонку по первой модели. При смене модели/размерности выберите **новое имя таблицы** одновременно в core и ingestion, затем запустите ingestion заново. Не смешивайте размерности в одной таблице.
- Повторный запуск insertion добавляет копии. Для чистого повторного наполнения используйте новое имя таблицы либо только утверждённую ИТ очистку прежней.

Значения Runtime configuration удобны для первого UI-теста, но сохраняются в workflow. Перед production ИТ должны перенести ключ FastAPI/MAS в approved credential or secret store и ограничить редактирование workflow.

### Администраторский Compose импорт (опционально)

Если ИТ использует reference Compose, импорт можно делать CLI, но это **не требуется** для рабочего UI-only сценария:

```bash
docker compose exec -T n8n n8n import:workflow --input=/workflows/excel-extraction-agent.workflow.json
docker compose exec -T n8n n8n import:workflow --input=/workflows/excel-rag-ingestion.workflow.json
```

После CLI import workflow деактивирован: опубликуйте/активируйте его в UI. Старый `context-seeder` остаётся только как OpenAI/Compose convenience option; не используйте его для корпоративного UI-only deployment.

## API examples

```bash
# New extraction
curl -X POST http://localhost:${N8N_HOST_PORT:-5678}/webhook/excel-extract \
  -H 'X-Excel-Webhook-Key: <webhook-secret>' \
  -F 'file=@orders.xlsx' \
  -F 'request={"prompt":"Extract paid orders with Order ID, Customer and Amount as CSV."}'

# Resume a clarification; do not send file again
curl -X POST http://localhost:${N8N_HOST_PORT:-5678}/webhook/excel-extract \
  -H 'X-Excel-Webhook-Key: <webhook-secret>' \
  -F 'session_id=sess_...' \
  -F 'clarification_response={"token":"clr_...","answers":[{"question_id":"table_selection","answer":"Sales rep, Region, Revenue"}]}'
```

The authenticated form is at `http://localhost:${N8N_HOST_PORT:-5678}/form/excel-extract-form`.

## Verification

```bash
docker compose --env-file .env config --quiet
docker compose ps

docker build --target test -t excel-tools-test ./excel-agent-tools
docker run --rm excel-tools-test

# n8n external runner should be healthy; logs must show “n8n Task Broker ready”
docker compose logs --tail=100 n8n n8n-runners

# Vector context
set -a; . ./.env; set +a
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "select metadata->>'slug', vector_dims(embedding) from n8n_excel_agent_context order by 1;"
```

`excel-agent-tools/tests/fixtures/complex/` contains ten deterministic non-sensitive `.xlsx` fixtures: preambles/titles, side-by-side tables, multi-row headers, late headers, grouped headers, several internal blank rows plus total rows, Unicode/hidden sheets, sparse layouts, duplicate headers, and date/number/boolean data. The Docker test image executes the full deterministic tool flow across all ten.

## Operational controls and remaining deployment responsibility

- FastAPI sessions/artifacts are disposable after `SESSION_TTL_HOURS` (24 hours by default); n8n execution data is pruned after 168 hours by default. Treat both as potentially sensitive data and set retention to policy.
- Back up and restore-test `postgres_data` and `n8n_data`; do not back up `excel_sessions` unless retention policy requires it. Encrypt backups.
- Alert on all container health checks, Docker volume free space, n8n failed executions, FastAPI 4xx/5xx, OpenAI failures/latency, and runner disconnects.
- Use a managed secret store, TLS, reverse-proxy rate limits/WAF, centralized logs, and an incident/restore runbook before exposing the host publicly.
- Image digests are intentionally pinned. Review CVEs and deliberately update/retest pinned images as part of patch management.

The stack is production-oriented and E2E-tested, but production sign-off remains an operator responsibility until the public TLS/proxy, secret manager, backup/restore test, and monitoring/alerting are deployed in the target environment.
