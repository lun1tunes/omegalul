# MAS Activity Service

Live chat-style presentation of Orchestrator ↔ specialist handoffs with in-chat HITL (reply / approve / reject).

## Run

```bash
cd mas-activity-service
python3 -m pip install -r requirements.txt
# optional: source ../.env for N8N_* if you want live Orchestrator proxy
MAS_ACTIVITY_KEY=dev-local HITL_MODE=local PYTHONPATH=. python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8200
# tests: PYTHONPATH=. python3 -m pytest -q tests/test_activity_api.py
```

Open [http://127.0.0.1:8200/](http://127.0.0.1:8200/) → **Seed demo**, or `/t/<task_id>`.

Demo ends on an open release gate — use the composer to Approve / Reject / Reply.

Knowledge UI: [http://127.0.0.1:8200/knowledge](http://127.0.0.1:8200/knowledge) (nav **База знаний**). Edits write the authoring JSON only; re-run n8n **Knowledge Ingestion** to refresh PG / PGVector.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/turns` | `X-Activity-Key` | one handoff turn |
| `POST` | `/v1/sync` | `X-Activity-Key` | batch (from Trace Writer); optional `human_gate`/`status` |
| `GET` | `/v1/tasks/{id}` | — | snapshot + gate for UI |
| `GET` | `/v1/tasks/{id}/gate` | — | gate; `?refresh=1` pulls Orchestrator status when configured |
| `POST` | `/v1/tasks/{id}/hitl` | `X-Activity-Key` | `reply` / `approve` / `reject` / `cancel` / `status` |
| `GET` | `/v1/tasks/{id}/stream` | — | SSE: turns + gate updates |
| `POST` | `/v1/demo/seed` | `X-Activity-Key` | presentation fixture with open HITL gate |
| `GET` | `/v1/knowledge/namespaces` | — | agent bases for Knowledge UI |
| `GET` | `/v1/knowledge/documents` | — | list cards (`?target_base=`) |
| `GET` | `/v1/knowledge/documents/{base}/{id}` | — | full card |
| `POST` | `/v1/knowledge/documents` | `X-Activity-Key` | create card (`text` + tags) |
| `PATCH` | `/v1/knowledge/documents/{base}/{id}` | `X-Activity-Key` | update `text` / `title` / `keywords` / `topics` / `task_patterns` |

Default key: `dev-local` (`MAS_ACTIVITY_KEY`).

HITL body:

```json
{
  "action": "approve",
  "requested_by": "И. Иванов",
  "human_response": "optional for approve/reject; required for reply",
  "gate_id": null,
  "expected_version": null
}
```

`gate_id` / `expected_version` are auto-filled from status when Orchestrator backend is live; named `requested_by` is required (not `anonymous`).

## Knowledge corpus

| Env | Default | Purpose |
|---|---|---|
| `MAS_KNOWLEDGE_CORPUS` | `../n8n/rag/excel-agent-operating-guide.documents.json` | Authoring SoT path (read/write) |

Saving a card updates JSON only (`text`, `revision`, `source_hash`, related metadata hashes). It does **not** start Knowledge Ingestion; run that workflow separately so parents + PGVector stay in sync.

## HITL backends

| `HITL_MODE` | Behavior |
|---|---|
| `auto` (default) | webhook → n8n REST → local |
| `local` | in-memory gate (demo / offline UI) |
| `webhook` | `ORCHESTRATOR_WEBHOOK_URL` (+ optional `ORCHESTRATOR_AUTH_HEADER` / `ORCHESTRATOR_AUTH_VALUE`) |
| `n8n_rest` | login to `N8N_BASE_URL` with `N8N_USERNAME` / `N8N_PASSWORD`, run `ORCHESTRATOR_WORKFLOW_ID` |

## n8n wiring

`Writer — MAS Trace` posts `event_type=handoff` events to `/v1/sync` after durable insert.

After import, edit Code node **Prepare MAS activity sync**:

- `ACTIVITY_BASE_URL` — e.g. `http://host.docker.internal:8200` if n8n is in Docker on Linux/Windows
- `ACTIVITY_KEY` — same as `MAS_ACTIVITY_KEY`

HTTP node uses `continueOnFail` / `neverError`: Trace Writer stays durable if the UI service is down.

Optionally extend sync payload with `status` + `human_gate` when Orchestrator opens a gate so the UI composer arms without a manual refresh.

## Presentation fields (v1.1)

Each turn is enriched before UI:

| Field | Meaning |
|---|---|
| `brief` | 1–4 предложения по-русски: что произошло (шаблон по status, если нет); keyword/поля — латиницей |
| `at_abs` | Absolute Tyumen time (`YYYY-MM-DD HH:MM:SS Тюмень`, Asia/Yekaterinburg) |
| `duration_ms` / `duration_label` | Specialist wall time until this handoff |
| `outcome` | `ok` / `wait` / `block` / `info` — left border only |
| `chips` | Allowlisted detail keys only (no secrets/prompts) |

Human turns use statuses `HUMAN_REPLY` / `HUMAN_APPROVED` / `HUMAN_REJECTED`.

## Why this shape

- Handoffs already exist in `runtime_json.handoff_events` / `mas_trace_event`.
- Trace Writer is the single choke point that sees every handoff batch without sprinkling HTTP into every Orchestrator Code node.
- HITL mirrors Form — MAS Human Gate: status first, then resume with auto `gate_id` / `expected_version`.
- UI stays a light transcript + one composer for accountable human actions.
