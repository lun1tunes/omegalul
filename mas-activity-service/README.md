# MAS Activity Service

Live chat-style presentation of Orchestrator ↔ specialist handoffs.

## Run

```bash
cd mas-activity-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
MAS_ACTIVITY_KEY=dev-local .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8200
```

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) → **Seed demo**, or `/t/<task_id>`.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/turns` | `X-Activity-Key` | one handoff turn |
| `POST` | `/v1/sync` | `X-Activity-Key` | batch (from Trace Writer) |
| `GET` | `/v1/tasks/{id}` | — | snapshot for UI |
| `GET` | `/v1/tasks/{id}/stream` | — | SSE live updates |
| `POST` | `/v1/demo/seed` | `X-Activity-Key` | presentation fixture |

Default key: `dev-local` (`MAS_ACTIVITY_KEY`).

## n8n wiring

`Writer — MAS Trace` posts `event_type=handoff` events to `/v1/sync` after durable insert.

After import, edit Code node **Prepare MAS activity sync**:

- `ACTIVITY_BASE_URL` — e.g. `http://host.docker.internal:8200` if n8n is in Docker on Linux/Windows
- `ACTIVITY_KEY` — same as `MAS_ACTIVITY_KEY`

HTTP node uses `continueOnFail` / `neverError`: Trace Writer stays durable if the UI service is down.

## Presentation fields (v1.1)

Each turn is enriched before UI:

| Field | Meaning |
|---|---|
| `brief` | 1–4 sentences: what just happened (template by status if missing) |
| `at_abs` | Absolute UTC timestamp (`YYYY-MM-DD HH:MM:SS UTC`) |
| `duration_ms` / `duration_label` | Specialist wall time until this handoff |
| `outcome` | `ok` / `wait` / `block` / `info` — left border only |
| `chips` | Allowlisted detail keys only (no secrets/prompts) |

## Why this shape

- Handoffs already exist in `runtime_json.handoff_events` / `mas_trace_event`.
- Trace Writer is the single choke point that sees every handoff batch without sprinkling HTTP into every Orchestrator Code node.
- UI stays a light transcript: brief, absolute time, duration, from→to, status, safe chips.
