# MAS Activity Service

Live chat-style presentation of Orchestrator ↔ specialist handoffs with in-chat HITL and **new-task start** (Entry-shaped, drag-and-drop files).

Два равноправных режима: **Docker Compose** и **Windows CMD** (полевые условия, только командная строка).

## Windows CMD

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
| `MAS_ACTIVITY_HOST`, `MAS_ACTIVITY_PORT` | listen address (`.bat`) |
| `HITL_MODE` | `local` / `webhook` / `n8n_rest` / `auto` |

## Docker Compose

```bash
# from repo root; requires MAS_ACTIVITY_KEY in .env
docker compose up -d --build mas-activity
curl -sS http://127.0.0.1:8200/health
```

## UI

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) → **Новая задача** or seed via API, or `/t/<task_id>`.

**Новая задача** — Entry-like composer (description + drag-and-drop Excel / SCHEDULE / `.dev` / CPS3). Live backends call Orchestrator `action=start`; `local` creates a presentation-only task.

HITL composer arms when status is `awaiting_human` / `AWAITING_HUMAN` and `human_gate` is set.

Knowledge UI: [http://127.0.0.1:8200/knowledge](http://127.0.0.1:8200/knowledge). Edits write authoring JSON only; re-run n8n **Knowledge Ingestion** to refresh PG / PGVector.

```bat
REM optional local uvicorn without .bat (Linux/macOS style)
set MAS_ACTIVITY_KEY=dev-local
set HITL_MODE=local
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8200
```

Tests: `.venv\Scripts\python.exe -m pytest -q` (19 passed).

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/turns` | `X-Activity-Key` | one handoff turn |
| `POST` | `/v1/sync` | key | batch (from Trace Writer); optional `human_gate`/`status` |
| `GET` | `/v1/tasks` | — | rail catalog |
| `GET` | `/v1/tasks/{id}` | — | snapshot + gate |
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

## n8n wiring

After import, edit Code node **Prepare MAS activity sync**:

- Compose DNS: `http://mas-activity:8200`
- Windows host from n8n Docker: `http://host.docker.internal:8200`
- Windows field PC IP: `http://<IP-Windows>:8200`

`ACTIVITY_KEY` must match `MAS_ACTIVITY_KEY`.

## Presentation fields (v1.1)

| Field | Meaning |
|---|---|
| `brief` | 1–4 предложения по-русски |
| `at_abs` | Absolute Tyumen time (`Asia/Yekaterinburg`) |
| `duration_ms` / `duration_label` | Specialist wall time until handoff |
| `outcome` | `ok` / `wait` / `block` / `info` |
| `chips` | Allowlisted detail keys only |

Human turns: `TASK_STARTED` / `HUMAN_REPLY` / `HUMAN_APPROVED` / `HUMAN_REJECTED`.
