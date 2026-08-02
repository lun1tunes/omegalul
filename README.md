# Excel extraction agent stack

Docker deployment for the technical task:

- **n8n 2.30.8** — agent/orchestration layer;
- **PostgreSQL 16 + pgvector** — n8n database, contextual memory and vector store;
- **Excel Tools FastAPI** — separate Docker container that stores sessions and executes deterministic Excel tools only. It does not call OpenAI/Qwen and has no agent loop.

## Start

```bash
cp .env.example .env
# edit every change-me value and public host values
# For the supplied Codex Sale provider, set QWEN_BASE_URL=https://codex.sale/v1,
# QWEN_API_KEY to its API key and QWEN_MODEL=qwen3.6-plus.
docker compose up -d --build
docker compose ps
```

Open n8n at `http://localhost:${N8N_HOST_PORT:-5678}`. The service is at `http://localhost:${EXCEL_TOOLS_HOST_PORT:-8000}` and its OpenAPI UI is `/docs`.

All host ports are bound to `127.0.0.1`; use a reverse proxy with TLS if external access is needed.

## n8n configuration

1. In n8n, create a PostgreSQL credential using `postgres:5432` plus `POSTGRES_*` from `.env`.
2. Use it in **Postgres Chat Memory** for context memory and **PGVector Vector Store** for vector storage. The init script enables `vector` automatically.
3. Create an **OpenAI-compatible protocol** credential named `Qwen Codex Sale` with Base URL `${QWEN_BASE_URL}`, API key `${QWEN_API_KEY}` and model `${QWEN_MODEL}` in the **Qwen Chat Model**. This is a Qwen provider connection, not OpenAI.
4. For **Qwen Embeddings**, configure a *separate* credential for a provider that exposes `/embeddings` (for example, self-hosted `Qwen/Qwen3-Embedding-8B`); the supplied Codex Sale catalog exposes chat Qwen models only, so it must not be used as an embeddings credential.
5. Import `n8n/workflows/ai-components.workflow.json` to get the **AI Agent**, **Postgres Chat Memory**, **PGVector Vector Store**, Qwen chat model and embedding-node wiring; replace its credential placeholders. The Excel workflow intentionally performs the multi-tool loop in its Code node because the FastAPI tool schemas are dynamic per service.
6. Import `n8n/workflows/excel-extraction-agent.workflow.json` and activate it. The webhook receives an Excel file as binary field `file` plus JSON request data.

The workflow makes the tool loop itself: upload binary to FastAPI, load `/tools`, call the Qwen OpenAI-compatible endpoint, execute tool calls with `/tools/batch`, append `tool` messages, and only returns an output produced by `finalize_extraction`. It caps iterations at 12 and total calls at 30.

`N8N_BLOCK_ENV_ACCESS_IN_NODE=false` is set because this imported local Code node reads the Docker-injected Qwen and Excel Tools configuration. Keep n8n private (the compose ports bind to loopback) and do not grant editor access to untrusted users; use n8n credentials/HTTP Request nodes instead if that trust boundary is required.


## Qwen access

`codex-sale-credentials.md` contains an OpenAI-compatible provider configuration. Its catalog was checked and includes `qwen3.5-plus`, `qwen3.6-plus`, `qwen3.7-plus` and `qwen3.7-max`; this stack defaults to `qwen3.6-plus`. Put the key **only** in `.env` as `QWEN_API_KEY`; the credentials file and `.env` are ignored by Git.

A real end-to-end webhook run was made with Microsoft’s public Financial Sample Excel workbook: n8n sent the file to the FastAPI container (a 83,418-byte `candidate.xlsx` session was created), then called Codex Sale. `/v1/models` returned the Qwen catalog, but `/v1/chat/completions` returned provider HTTP 429 (`upstream_busy`). Thus Docker, binary upload, session creation and orchestration are proven; model inference needs to be retried after the provider's temporary rate limit clears.

For the exact public weights requested, [`Qwen/Qwen3.6-27B`](https://huggingface.co/Qwen/Qwen3.6-27B) is public (Apache-2.0) and its card documents the OpenAI-compatible Model Studio ID `qwen3.6-27b` as well as self-hosting with vLLM/SGLang. The standard checkpoint is approximately **51.7 GiB** of safetensors and the official SGLang/vLLM examples use tensor parallelism on 8 GPUs, so it was not downloaded or started on this host (no NVIDIA GPU is available). Point `QWEN_BASE_URL` to a managed Model Studio endpoint or a separately provisioned vLLM/SGLang server to use the exact 27B model.

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
docker build --target test -t excel-tools-test ./excel-agent-tools
docker run --rm excel-tools-test
curl http://localhost:${EXCEL_TOOLS_HOST_PORT:-8000}/health
curl http://localhost:${N8N_HOST_PORT:-5678}/healthz
# check pgvector
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
```
