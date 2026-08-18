# MAS Activity Service

Live chat-style presentation of Orchestrator ↔ specialist handoffs with in-chat HITL and **new-task start** (Entry-shaped, drag-and-drop files).

**На работе:** только Windows CMD (ниже). n8n — корпоративный UI-импорт; Activity не поднимают из Docker на полевом ПК.  
Полный порядок развёртывания: [`../docs.md`](../docs.md) §0.

## Windows CMD (канон)

```bat
cd mas-activity-service
setup-windows.bat
copy mas-activity.env.example mas-activity.env
notepad mas-activity.env
start-windows.bat
```

Проверка во втором CMD: `check-windows.bat` (`/health` + `/ready`).

Локально: `http://127.0.0.1:8200/`. Для удалённого n8n: `MAS_ACTIVITY_HOST=0.0.0.0`. В n8n откройте ноду **Activity connection** и поставьте `activity_base_url=http://<IP-Windows>:8200`.

`start-windows.bat` **не парсит** env. Python читает `mas-activity.env` сам (как excel-tools), плюс `mas-activity-service/.env` и корневой `.env`, без перезаписи уже заданных переменных процесса.

| Env | Purpose |
|---|---|
| `MAS_ACTIVITY_HOST`, `MAS_ACTIVITY_PORT` | listen address (`python -m app`) |
| `LOG_LEVEL` | `INFO` / `DEBUG` |
| `ACTIVITY_TLS_VERIFY` | `true` by default; set `false` only for a trusted local/self-signed HTTPS endpoint |
| `ACTIVITY_CA_BUNDLE` | optional CA PEM path; preferred over disabling verification for corporate PKI |
| `ORCHESTRATOR_WEBHOOK_URL` | боевой вызов оркестратора (предпочтительно) |
| `ORCHESTRATOR_AUTH_*` | inbound header auth **n8n webhook**, не Activity |
| `N8N_BASE_URL` + `N8N_USERNAME` / `N8N_PASSWORD` | REST-фолбэк, если webhook не задан |
| `ACTIVITY_LIST_URL` / `ACTIVITY_FEED_URL` | hydrate Data Tables; иначе выводятся из хоста webhook/`N8N_BASE_URL` |

Авторизации внутри Activity **нет**. ФИО инженера (`requested_by`) — поле формы, не ключ.

После UI-импорта hydrate-workflow и биндинга Data Tables задайте webhook URL на **корпоративный n8n** (не `http://n8n:5678` — это только Compose DNS).

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

Compose по умолчанию бьёт в `http://n8n:5678/webhook/...`. Импорт + bind Data Tables в n8n UI, затем activate hydrate-workflow.

Activity пишет `/data/activity_state.json` (volume `activity_data`), чтобы recreate не обнулял rail. Файл runtime-only — не коммитить.

## UI

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) → **Новая задача** или `/t/<task_id>`.

Когда у задачи есть SCHEDULE, в ленте **Скачать .INC** → `GET /v1/tasks/{id}/schedule`.

**MAS / Activity** (бренд слева) — обновляет rail из n8n Data Tables. То же при перезагрузке (`?durable=1`).

**Новая задача** сразу пишет `TASK_STARTED` / `ORCH_DISPATCHED` в ленту Activity и отвечает `accepted` (id `act_…`). Вызов Orchestrator `action=start` идёт в фоне. Если n8n не задан, старт отвечает **503** с текстом, что прописать в `mas-activity.env`. Живой чат: SSE `GET /v1/tasks/{id}/stream` плюс Trace Writer `POST /v1/sync` — без перезагрузки страницы.

HITL composer открывается при `awaiting_human` и заполненном `human_gate`. Ответ человека пишется в ленту **до** вызова n8n.

Knowledge UI: [http://127.0.0.1:8200/knowledge](http://127.0.0.1:8200/knowledge) — правка `excel-agent-operating-guide.documents.json`. Кнопка **Загрузить в RAG** шлёт весь корпус в n8n `POST /webhook/mas-knowledge-ingest` (workflow `MAS — Knowledge Ingestion` должен быть Active). Ответ: сколько добавлено / уже было / всего в RAG. Чтобы обновить уже залитую карточку, поднимите `revision`.

```bat
REM прямой запуск без .bat — тот же load mas-activity.env
.venv\Scripts\python.exe -m app
```

Tests: `.venv\Scripts\python.exe -m pytest -q`.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | процесс жив (liveness), без прозвона n8n |
| `GET` | `/ready` | UI + n8n `/healthz` + orchestrator + list/feed webhooks; иначе 503 |
| `GET` | `/v1/diagnostics/connectivity` | подробный отчёт тех же проб |
| `POST` | `/v1/turns` | one handoff turn |
| `POST` | `/v1/sync` | batch (from Trace Writer) |
| `POST` | `/v1/hydrate` | list/feed payload from n8n |
| `GET` | `/v1/tasks` | rail; `?durable=1` pulls list webhook |
| `GET` | `/v1/tasks/{id}` | snapshot + gate |
| `GET` | `/v1/tasks/{id}/schedule` | download captured SCHEDULE |
| `POST` | `/v1/tasks/start` | multipart start; сразу `accepted` + `act_…`, оркестратор в фоне |
| `POST` | `/v1/tasks/{id}/hitl` | `reply` / `approve` / `reject` / `cancel` / `status` |
| `GET` | `/v1/tasks/{id}/stream` | SSE (snapshot / turn / gate); turn несёт status и HITL-gate |
| `POST` | `/v1/knowledge/ingest` | live corpus → n8n Knowledge Ingestion webhook |

`POST /v1/sync` не продвигает routine handoff-статусы в статус задачи и не закрывает открытый gate. Хэндоффы из Trace Writer всё равно попадают в ленту и уходят в SSE.

### Start body (multipart)

| Field | Required | Notes |
|---|---|---|
| `task_description` | yes | objective |
| `requested_by` | yes | named engineer |
| `schedule_root` | no | root filename hint |
| `file` | no | Excel `.xlsx`/`.xls` |
| `schedule_files` | no | multi SCHEDULE fragments |
| `trajectory_files` | no | multi `.dev` |
| `surface_file` | no | CPS3 surface |

Старт/HITL идут в n8n: webhook, если задан `ORCHESTRATOR_WEBHOOK_URL` (или выведен из `N8N_BASE_URL`); иначе REST при `N8N_BASE_URL` + user/password. Иначе 503, без «локального» режима.

## n8n wiring

После UI-импорта откройте **Activity connection** (Set node) в `Writer — MAS Trace` и в `Error — MAS Case Handler`:

- Полевой Windows: `http://<IP-Windows>:8200`
- Compose DNS (лаборатория): `http://mas-activity:8200`
- n8n в Docker → Activity на хосте: `http://host.docker.internal:8200`

Ключ Activity не нужен.

## CORS

Морда бьёт в те же `/v1/...` (тот же origin) — CORS не нужен, пока UI открыт с `:8200`. Middleware стоит, чтобы не ломались превью/другой хост.

Starlette/FastAPI: **`allow_origins=["*"]` нельзя сочетать с `allow_credentials=True`** — процесс падает или браузер игнорирует заголовки. Сейчас credentials выключены (куков нет).

Если в консоли браузера `blocked by CORS policy`:

1. Перезапусти Activity после правки `app/main.py`.
2. Блок `CORSMiddleware` должен остаться сразу после `app = FastAPI()`, и это должен быть **последний** `add_middleware` (он становится внешним слоем).
3. В ошибке смотри `Access-Control-Allow-Origin`. Чтобы пустить только морду: замени `"*"` на точный origin из сообщения (`http://127.0.0.1:8200`, без слэша в конце). Тогда можно `allow_credentials=True`.
4. Браузер морды **не** ходит в n8n webhook. Галочка CORS на ноде Webhook в n8n для Activity не нужна (Code-ноды зовут Activity с сервера).

## Presentation fields (v1.1)

| Field | Meaning |
|---|---|
| `brief` | лаконичный русский шаблон по `status` |
| `at_abs` | Absolute local time with UTC offset |
| `duration_ms` / `duration_label` | Wall time until handoff |
| `outcome` | `ok` / `wait` / `block` / `info` |
| `chips` | Allowlisted detail keys only |
