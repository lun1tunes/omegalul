# MAS Activity Service

Чат-лента Orchestrator ↔ specialists, HITL в том же UI, старт задачи drag-and-drop.

**На работе:** только Windows CMD (ниже). n8n — корпоративный UI-импорт; Activity не поднимают из Docker на полевом ПК.  
Полный порядок: [`../docs.md`](../docs.md) §0–§2.

Живой вход — **`POST /cases`**. Стейт кейсов — Postgres за `MAS — Control Plane Proxy`. Data Tables / `Activity — Hydrate` / Trace Writer **не используются**. Не задавать `ACTIVITY_HYDRATE_URL`.

## Windows CMD (канон)

```bat
cd mas-activity-service
setup-windows.bat
copy mas-activity.env.example mas-activity.env
notepad mas-activity.env
start-windows.bat
```

Проверка во втором CMD: `check-windows.bat` (`/health` + `/ready`).

Локально: `http://127.0.0.1:8200/`. Для удалённого n8n: `MAS_ACTIVITY_HOST=0.0.0.0`. В n8n **Activity connection** / `activity_base_url` = `http://<IP-Windows>:8200`.

`start-windows.bat` **не парсит** env. Python читает `mas-activity.env` сам, плюс `mas-activity-service/.env` и корневой `.env`, без перезаписи уже заданных переменных процесса.

| Env | Purpose |
|---|---|
| `MAS_ACTIVITY_HOST`, `MAS_ACTIVITY_PORT` | listen address (`python -m app`) |
| `LOG_LEVEL` | `INFO` / `DEBUG` |
| `ACTIVITY_TLS_VERIFY` | `true` по умолчанию; `false` только для доверенного local/self-signed HTTPS |
| `ACTIVITY_CA_BUNDLE` | optional CA PEM; лучше, чем отключать проверку |
| `ORCHESTRATOR_WEBHOOK_URL` | `…/webhook/mas-orchestrator-step` |
| `CONTROL_PLANE_REQUIRED` | `true` на поле; без прокси процесс не стартует |
| `CONTROL_PLANE_PROXY_URL` | `…/webhook/mas-control-plane` (cases, events, HITL, artifacts) |
| `CONTROL_PLANE_PROXY_AUTH_*` | Header Auth прокси |
| `ORCHESTRATOR_AUTH_*` | Header Auth webhook оркестратора, не Activity |
| `KNOWLEDGE_INGEST_URL` | `…/webhook/mas-knowledge-ingest` (кнопка «Загрузить в RAG») |
| `N8N_BASE_URL` + user/password | REST-фолбэк, если webhook не задан |

Авторизации внутри Activity **нет**. ФИО инженера (`requested_by`) — поле формы, не ключ.

Для корпоративного контура импортируйте и **активируйте первым** `n8n/workflows/core/mas-control-plane-proxy.workflow.json`, затем `POST {"operation":"schema"}`.  
`/health` должен вернуть `control_plane_backend: "n8n_proxy"`. Activity к Postgres не подключается.

## Linux / macOS (лаборатория)

```bash
cd mas-activity-service
./setup-linux.sh
# отредактируйте mas-activity.env
./start-linux.sh
```

Если Compose уже держит `:8200`: `docker compose stop mas-activity`.

## Docker Compose (лаборатория, не полевой канон)

```bash
docker compose up -d --build mas-activity
curl -sS http://127.0.0.1:8200/health
curl -sS http://127.0.0.1:8200/ready
```

Compose бьёт в `http://n8n:5678/webhook/mas-control-plane`. Сначала импорт + activate прокси, потом контейнер Activity (см. `scripts/lab_soft_redeploy.py`). FastAPI **без `--reload`**: после правки Python — `docker compose up -d --force-recreate mas-activity`.

Activity пишет `/data/activity_state.json` (volume `activity_data`), чтобы recreate не обнулял rail. Runtime-only — не коммитить.

## UI

Открыть [http://127.0.0.1:8200/](http://127.0.0.1:8200/) → **Новая задача**. После правок static — hard-refresh (`app.js?v=…` в `index.html`).

Когда у задачи есть результат SCHEDULE, в шапке **Скачать .INC** → `GET /cases/{id}/schedule`.

**Новая задача** = `POST /cases` (multipart). Оркестратор `action=start` идёт в фоне. Без `ORCHESTRATOR_WEBHOOK_URL` старт отвечает **503**. Живая лента: SSE `GET /cases/{id}/stream`. Счётчик «N turn» на рейле — длина **свёрнутой** ленты, не сырой `COUNT(*)` событий.

HITL composer открывается при `waiting_user`. Ответ: `POST /cases/{id}/answer`.

Knowledge UI: [http://127.0.0.1:8200/knowledge](http://127.0.0.1:8200/knowledge) — правка `excel-agent-operating-guide.documents.json`. **Загрузить в RAG** → n8n `POST /webhook/mas-knowledge-ingest`. Маршрутизация оркестратора от RAG **не** зависит (`agent_registry`).

```bat
REM прямой запуск без .bat — тот же load mas-activity.env
.venv\Scripts\python.exe -m app
```

Tests: `.venv\Scripts\python.exe -m pytest -q`.

## API (живой путь)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | процесс жив (liveness) |
| `GET` | `/ready` | UI + webhooks оркестратора/прокси |
| `POST` | `/cases` | старт (multipart: `task_description`, `requested_by`, `file`, `schedule_files`, `schedule_root`, …) |
| `GET` | `/cases` | rail |
| `GET` | `/cases/{id}` | snapshot + свёрнутая лента + schema |
| `GET` | `/cases/{id}/stream` | SSE |
| `POST` | `/cases/{id}/answer` | HITL |
| `POST` | `/cases/{id}/run` | retry/restart |
| `GET` | `/cases/{id}/schedule` | скачать `.INC` |
| `GET` | `/cases/{id}/artifacts/{id}` | бинарники для Excel/Schedule FastAPI |
| `POST` | `/v1/knowledge/ingest` | live corpus → n8n Ingestion |

Пути `/v1/tasks/*`, `/v1/sync`, `/v1/hydrate` — совместимость со старым UI/Trace Writer. Живая морда их не зовёт.

Старт/HITL идут в n8n: webhook `ORCHESTRATOR_WEBHOOK_URL`; иначе REST при `N8N_BASE_URL` + user/password. Иначе 503.

## n8n wiring

`activity_base_url` в **MAS — Runtime Config** (ноды Runtime configuration / Runtime endpoints вызывают этот workflow):

- Полевой Windows: `http://<IP-Windows>:8200`
- Compose DNS: `http://mas-activity:8200`
- n8n в Docker → Activity на хосте: `http://host.docker.internal:8200`

Ключ Activity не нужен. Специалисты сами `GET /cases/{id}/artifacts/{id}`.

## CORS

Морда бьёт в `/cases/…` с того же origin. Middleware стоит для превью/другого хоста.

Starlette/FastAPI: **`allow_origins=["*"]` нельзя сочетать с `allow_credentials=True`**. Сейчас credentials выключены.

Если в консоли `blocked by CORS policy`:

1. Перезапусти Activity после правки `app/main.py`.
2. Блок `CORSMiddleware` сразу после `app = FastAPI()`, и это должен быть **последний** `add_middleware`.
3. Чтобы пустить только морду: точный origin (`http://127.0.0.1:8200`, без слэша). Тогда можно `allow_credentials=True`.
4. Браузер морды **не** ходит в n8n webhook.
