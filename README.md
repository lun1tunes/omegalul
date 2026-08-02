# Excel extraction agent stack

Docker deployment for the technical task:

- **n8n 2.30.8** — agent/orchestration layer;
- **PostgreSQL 16 + pgvector** — n8n database, contextual memory and vector store;
- **Excel Tools FastAPI** — separate Docker container that stores sessions and executes deterministic Excel tools only. It does not call OpenAI/Qwen and has no agent loop.

## Start

```bash
cp .env.example .env
# edit every change-me value, QWEN_BASE_URL, QWEN_API_KEY and public host values
docker compose up -d --build
docker compose ps
```

Open n8n at `http://localhost:5678`. The service is at `http://localhost:8000` and its OpenAPI UI is `/docs`.

All host ports are bound to `127.0.0.1`; use a reverse proxy with TLS if external access is needed.

## n8n configuration

1. In n8n, create a PostgreSQL credential using `postgres:5432` plus `POSTGRES_*` from `.env`.
2. Use it in **Postgres Chat Memory** for context memory and **PGVector Vector Store** for vector storage. The init script enables `vector` automatically.
3. Create OpenAI credentials pointing at `${QWEN_BASE_URL}` (OpenAI-compatible Qwen endpoint) and model `${QWEN_MODEL}`. Use this credential in the **OpenAI Chat Model** and **Embeddings OpenAI** nodes.
4. Import `n8n/workflows/excel-extraction-agent.workflow.json`, set its credentials, and activate it. The webhook receives an Excel file as binary field `file` plus JSON request data.

The workflow makes the tool loop itself: upload binary to FastAPI, load `/tools`, call the Qwen OpenAI-compatible endpoint, execute tool calls with `/tools/batch`, append `tool` messages, and only returns an output produced by `finalize_extraction`. It caps iterations at 12 and total calls at 30.

## Excel Tools API

Every `/api/v1/*` endpoint requires `X-API-Key: $EXCEL_TOOLS_API_KEY`.

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "X-API-Key: $EXCEL_TOOLS_API_KEY" \
  -F 'file=@orders.xlsx' \
  -F 'payload={"request":{"fields":["order_id","customer","amount"]}}'
```

The OpenAI tool schemas are available at `GET /api/v1/tools`. The implemented tools are:

`workbook_introspect`, `sheet_preview`, `detect_tables`, `describe_table`, `list_column_values`, `query_table`, `validate_result`, `export_result`, `get_session_state`, `save_agent_plan`, `submit_clarification`, `resolve_clarification`, `finalize_extraction`.

Session files are Docker-volume backed, API keys are checked, IDs/paths are validated, uploads stream to disk with a 200 MB default ceiling, and stale sessions are removed during uploads according to `SESSION_TTL_HOURS`.

## Verify

```bash
docker compose --env-file .env config
docker compose --env-file .env exec excel-tools python -m pytest -q /service/tests  # requires test image/override; see below
curl http://localhost:8000/health
```

For a host-independent test run, use the image test command documented in `excel-agent-tools/requirements-dev.txt`, or run the supplied integration test through a temporary dev container:

```bash
docker build -t excel-tools-test -f excel-agent-tools/Dockerfile excel-agent-tools
docker run --rm excel-tools-test python -m compileall -q app
```
