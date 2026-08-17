# MAS Activity Service

Live chat-style presentation of Orchestrator ↔ specialist handoffs with in-chat HITL and **new-task start** (Entry-shaped, drag-and-drop files).

**На работе:** только Windows CMD (ниже). n8n — корпоративный UI-импорт; Activity не поднимают из Docker на полевом ПК.  
Полный порядок развёртывания: [`../docs.md`](../docs.md) §3.

## Windows CMD (канон)

```bat
cd mas-activity-service
setup-windows.bat
copy mas-activity.env.example mas-activity.env
notepad mas-activity.env
start-windows.bat
```

Проверка во втором CMD: `check-windows.bat`.

Локально: `http://127.0.0.1:8200/`. Для удалённого n8n: `MAS_ACTIVITY_HOST=0.0.0.0` и в Trace Writer `ACTIVITY_BASE_URL=http://<IP-Windows>:8200`.

| Env | Purpose |
|---|---|
| `MAS_ACTIVITY_KEY` | `X-Activity-Key` (обязательно сменить пример) |
| `MAS_ACTIVITY_AUTH_DISABLED` | local development only; set `true` to disable `X-Activity-Key` checks |
| `MAS_ACTIVITY_HOST`, `MAS_ACTIVITY_PORT` | listen address (`.bat`) |
| `HITL_MODE` | `local` / `webhook` / `n8n_rest` / `auto` |
| `ACTIVITY_LIST_URL` | n8n webhook `mas-activity-list-tasks` (Data Table catalog) |
| `ACTIVITY_FEED_URL` | n8n webhook `mas-activity-load-feed` (CAS + trace → feed) |
| `ACTIVITY_DURABLE_AUTH_*` | optional header auth if webhooks are protected |

После UI-импорта hydrate-workflow и биндинга Data Tables задайте `ACTIVITY_LIST_URL` / `ACTIVITY_FEED_URL` на **URL корпоративного n8n** (не `http://n8n:5678` — это только Compose DNS).

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
# from repo root; requires MAS_ACTIVITY_KEY in .env
docker compose up -d --build mas-activity
curl -sS http://127.0.0.1:8200/health
```

Compose defaults `ACTIVITY_LIST_URL` / `ACTIVITY_FEED_URL` to `http://n8n:5678/webhook/...`. Import + bind Data Tables in n8n UI, then activate the two Activity hydrate workflows.

Activity also writes `/data/activity_state.json` (Compose volume `activity_data`) so **recreate/restart keeps the rail** even when hydrate webhooks are not active yet. That file is **runtime-only** — never commit it (`mas-activity-service/data/` is gitignored). Local Linux uses `ACTIVITY_STATE_PATH` in `mas-activity.env`.

Durable list hydrate (`?durable=1`) merges the newest CAS rows from Data Tables. Ghost prune runs only when the list page is **complete** (`count` ≤ returned tasks; the list workflow caps at 200). Truncated pages never evict older in-memory CAS tasks. Local `act_*` / `demo_*` presentation tasks are always kept (trim prefers dropping CAS rows when over `MAX_TASKS`). An empty in-memory rail triggers at most **one** automatic list pull without `?durable=1`; use brand click / page load (`durable=1`) to refresh again.

## UI

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) → **Новая задача** or seed via API, or `/t/<task_id>`.

When a task has a captured SCHEDULE result (Builder `deliverables[].schedule_text`, Orchestrator `release.schedule_text`, or `generated_schedule`), the feed shows **Скачать .INC** → `GET /v1/tasks/{id}/schedule`.

**MAS / Activity** (бренд слева) — обновляет rail (+ открытый feed) из n8n Data Tables. То же происходит при обычной перезагрузке страницы (`?durable=1` на старте).

**Новая задача** — Entry-like composer (description + drag-and-drop Excel / SCHEDULE / `.dev` / CPS3). Live backends call Orchestrator `action=start`; `local` creates a presentation-only task.

HITL composer arms when status is `awaiting_human` / `AWAITING_HUMAN` and `human_gate` is set.

Knowledge UI: [http://127.0.0.1:8200/knowledge](http://127.0.0.1:8200/knowledge). Edits write authoring JSON only; re-run n8n **Knowledge Ingestion** to refresh PG / PGVector.

```bat
REM optional local uvicorn without .bat
set MAS_ACTIVITY_KEY=dev-local
set HITL_MODE=local
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8200
```

При прямом запуске `python -m uvicorn app.main:app` сервис автоматически читает
`mas-activity-service/mas-activity.env`, затем `mas-activity-service/.env`, и не
перезаписывает переменные, уже переданные процессу. Это тот же подход, что и в
`excel-agent-tools`; корневой `.env` используется Docker Compose.

Tests: `.venv\Scripts\python.exe -m pytest -q` (22 passed).

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/turns` | `X-Activity-Key` | one handoff turn |
| `POST` | `/v1/sync` | key | batch (from Trace Writer); optional `human_gate`/`status` |
| `POST` | `/v1/hydrate` | key | apply list/feed payload from n8n hydrate workflows |
| `GET` | `/v1/tasks` | — | rail catalog; `?durable=1` pulls list webhook |
| `GET` | `/v1/tasks/{id}` | — | snapshot + gate; `?durable=1` or miss → feed webhook |
| `GET` | `/v1/tasks/{id}/schedule` | — | download captured SCHEDULE `.INC` (attachment) |
| `GET` | `/v1/tasks/{id}/gate` | — | gate; `?refresh=1` pulls Orchestrator status when configured |
| `POST` | `/v1/tasks/start` | `X-Activity-Key` | multipart start (Entry fields + files) |
| `POST` | `/v1/tasks/{id}/hitl` | `X-Activity-Key` | `reply` / `approve` / `reject` / `cancel` / `status` |
| `GET` | `/v1/tasks/{id}/stream` | — | SSE |
| `POST` | `/v1/demo/seed` | `X-Activity-Key` | presentation fixture with open HITL gate |

Default key in code fallback: `dev-local` (`MAS_ACTIVITY_KEY`). Windows `.env` requires a non-example key.

`POST /v1/sync` does **not** promote routine handoff statuses (e.g. `EXCEL_EVIDENCE_READY`) to task status or clear an open gate.

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

## HITL backends

| `HITL_MODE` | Behavior |
|---|---|
| `auto` (default) | webhook → n8n REST → local |
| `local` | in-memory gate + local start (demo / offline UI) |
| `webhook` | `ORCHESTRATOR_WEBHOOK_URL` (+ optional auth); start uses multipart when files attached |
| `n8n_rest` | `N8N_BASE_URL` + user/password + `ORCHESTRATOR_WORKFLOW_ID`; binaries as base64 on trigger item |

## Connectivity diagnostics

`GET /v1/diagnostics/connectivity` checks Activity → n8n Data Table hydrate webhooks and the configured MAS Orchestrator webhook without creating a task. Add `?task_id=<existing-task-id>` to also check the feed/trace Data Table webhook.

Проверить, что запущен именно новый процесс и env прочитан, можно через
`GET /health`: поле `auth_required` должно быть `false`, а `hitl_backend` —
`webhook` или `n8n_rest`.

When `MAS_ACTIVITY_AUTH_DISABLED=true`, the endpoint and the rest of the Activity API can be called without `X-Activity-Key`. This is for local development only; keep it `false` on any shared or corporate-facing instance.

## n8n wiring

После UI-импорта отредактируйте Code node **Prepare MAS activity sync**:

- Полевой Windows: `http://<IP-Windows>:8200`
- Compose DNS (лаборатория): `http://mas-activity:8200`
- n8n в Docker → Activity на хосте (лаборатория): `http://host.docker.internal:8200` (нужен доступ docker→host)

`ACTIVITY_KEY` must match `MAS_ACTIVITY_KEY`.

## Presentation fields (v1.1)

| Field | Meaning |
|---|---|
| `brief` | лаконичный русский шаблон по `status` (presentation layer) |
| `at_abs` | Absolute local time with UTC offset (`Asia/Yekaterinburg` → `UTC+5`) |
| `duration_ms` / `duration_label` | Wall time until handoff |
| `outcome` | `ok` / `wait` / `block` / `info` |
| `chips` | Allowlisted detail keys only |

Human turns: `TASK_STARTED` / `HUMAN_REPLY` / `HUMAN_APPROVED` / `HUMAN_REJECTED`.
