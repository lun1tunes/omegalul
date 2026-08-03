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

Open n8n at `http://localhost:${N8N_HOST_PORT:-5678}`. PostgreSQL and Excel Tools
are internal-only Docker services: they have no host ports and the Excel Tools
OpenAPI documentation is disabled by default.

For production, expose **only n8n** through an authenticated TLS reverse proxy;
set `N8N_PROTOCOL=https`, HTTPS `N8N_EDITOR_BASE_URL`/`N8N_WEBHOOK_URL`, and
`N8N_SECURE_COOKIE=true`. Keep the n8n container port loopback-only or remove its
host port entirely when the proxy shares the Docker network. The workflow also
requires `X-Excel-Webhook-Key: $EXCEL_WEBHOOK_API_KEY` on every extraction request.

## n8n configuration

1. The running local instance has encrypted n8n credentials **Qwen Codex Sale chat** and **Postgres pgvector**. The supplied Qwen key is not written to any repository file. On a fresh instance, create equivalents: `postgres:5432` plus `POSTGRES_*` from `.env`, and an **OpenAI-compatible** Qwen credential with Base URL `${QWEN_BASE_URL}` and `${QWEN_API_KEY}`. For production, put these values in a secret manager; n8n credentials are encrypted at rest by `N8N_ENCRYPTION_KEY`.
2. **Postgres Chat Memory** and **PGVector Vector Store** are bound to **Postgres pgvector**. The init script enables the `vector` extension automatically.
3. **Qwen Chat Model** is bound to **Qwen Codex Sale chat** with `qwen3.6-plus`. It is a Qwen provider connection, not OpenAI.
4. For **Qwen Embeddings**, configure a *separate* credential for a provider that exposes `/embeddings` (for example, self-hosted `Qwen/Qwen3-Embedding-8B`); the supplied Codex Sale catalog exposes chat Qwen models only, so it must not be used as an embeddings credential.
5. Import `n8n/workflows/ai-components.workflow.json` to get the **AI Agent**, **Postgres Chat Memory**, **PGVector Vector Store**, Qwen chat model and embedding-node wiring. The Excel workflow intentionally performs the multi-tool loop in its Code node because the FastAPI tool schemas are dynamic per service.
6. Import `n8n/workflows/excel-extraction-agent.workflow.json` and activate it. It has **three equivalent entry points** that normalize to one Excel-agent loop:
   - **HTTP** — `POST /webhook/excel-extract`, protected by `X-Excel-Webhook-Key`; send multipart field `file` and `request`.
   - **Form** — `GET /form/excel-extract-form`; upload one workbook and write a request in the browser. It requires a logged-in n8n user, so no webhook secret is exposed in page HTML.
   - **Call another workflow** — select this workflow in n8n’s **Execute Sub-workflow** node; pass `binary.file` and a `request` object (or a `session_id` plus `clarification_response` to continue a clarification).
   In every route, a clarification is resumed with the original `session_id` and `clarification_response`, without re-uploading the workbook.
7. `n8n/workflows/ai-components.workflow.json` is a reference/template for the required AI Agent, PostgreSQL memory, pgvector and embeddings wiring; it is inactive by design and its embeddings credential placeholder **must** be replaced with a real embeddings provider before enabling it. It is not part of the production extraction webhook path.

Example requests:

```bash
# New extraction
curl -X POST http://localhost:${N8N_HOST_PORT:-5678}/webhook/excel-extract \
  -H 'X-Excel-Webhook-Key: <your-webhook-secret>' \
  -F 'file=@orders.xlsx' \
  -F 'request={"fields":["order_id","amount"],"prompt":"Extract paid orders"}'

# Open the authenticated browser form
# http://localhost:${N8N_HOST_PORT:-5678}/form/excel-extract-form
#
# Resume a clarification returned by the first request
curl -X POST http://localhost:${N8N_HOST_PORT:-5678}/webhook/excel-extract \
  -H 'X-Excel-Webhook-Key: <your-webhook-secret>' \
  -F 'session_id=sess_...' \
  -F 'clarification_response={"token":"clr_...","answers":[{"question_id":"amount_column","answer":"Сумма итого"}]}'
```

When an extraction needs input, the response has `status: "clarification_needed"`,
`clarification.token`, the question list, and `meta.session_id`. Send that exact
session ID and an answer for **every** question to resume; the workbook is retained
in the Docker-backed Excel Tools session and must not be uploaded again. The
continuation performs `resolve_clarification` before returning to the model/tool loop.

The workflow makes the tool loop itself: upload binary to FastAPI, load `/tools`, call the Qwen OpenAI-compatible endpoint, execute tool calls with `/tools/batch`, append `tool` messages, and only returns an output produced by `finalize_extraction`. It caps iterations at 12 and total calls at 30.

### Calling it from another n8n workflow

In a caller workflow, add **Execute Sub-workflow**, choose **Excel extraction agent — FastAPI tools + Qwen loop**, and use “Run once with all items”. Pass the Excel binary as `file` and JSON such as:

```json
{
  "request": {
    "fields": ["order_id", "amount"],
    "prompt": "Extract paid orders"
  }
}
```

The child workflow returns the same structured result as the HTTP route. For a clarification continuation, pass `{ "session_id": "sess_...", "clarification_response": { ... } }` and no binary file.

`N8N_BLOCK_ENV_ACCESS_IN_NODE=false` is set because this imported local Code node reads the Docker-injected Qwen and Excel Tools configuration. Keep n8n private (the compose ports bind to loopback) and do not grant editor access to untrusted users; use n8n credentials/HTTP Request nodes instead if that trust boundary is required.

## Production operations checklist

- Replace every `change-me-*` value with independently generated secrets and store
  them in a secret manager or protected deployment environment; never commit `.env`.
- Use HTTPS URLs and `N8N_SECURE_COOKIE=true` behind a TLS reverse proxy. Apply
  request-size and rate limits there, especially to `/webhook/excel-extract`.
- Back up the `postgres_data` and `n8n_data` Docker volumes, encrypt backups, and
  test restoration. `excel_sessions` is intentionally disposable after its TTL.
- Pin image digests as in `docker-compose.yml`; review and deliberately update
  them as part of a vulnerability/patch-management process.
- Alert on container health, disk usage for Docker volumes, n8n failed executions,
  and provider errors. The current Qwen provider has returned `429 upstream_busy`,
  so production needs retry/backoff or a provider SLO before relying on it.


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

Session files are Docker-volume backed, API keys are checked, IDs/paths are validated,
and per-session tool calls are serialized to prevent concurrent state loss. Uploads
stream to disk with a 200 MB default ceiling; OOXML archive entry and expanded-size
limits are enforced before parsing; stale-session cleanup runs in a background task
according to `SESSION_TTL_HOURS`. CSV export neutralizes spreadsheet formulas.

## Verify

```bash
docker compose --env-file .env config
docker build --target test -t excel-tools-test ./excel-agent-tools
docker run --rm excel-tools-test
curl http://localhost:${N8N_HOST_PORT:-5678}/healthz
# check pgvector
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
docker compose exec excel-tools python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

## Complex Excel regression corpus

`excel-agent-tools/tests/fixtures/complex/` contains ten small, deterministic,
non-sensitive `.xlsx` files plus `manifest.json`. They cover merged titles and
headers, preambles, headers away from row one, horizontal tables on one sheet,
internal blank rows and total rows, duplicate column names, Unicode/hidden sheets,
sparse layouts, and dates/numbers/booleans. Regenerate only when intentionally
changing the fixtures:

```bash
python excel-agent-tools/tests/fixtures/complex/generate_corpus.py
docker build --target test -t excel-tools-test ./excel-agent-tools
docker run --rm excel-tools-test
```

The manifest-driven test performs `workbook_introspect → detect_tables →
describe_table → query_table → validate_result → export_result` for every expected
table. `detect_tables` streams worksheets with one-row look-ahead, flattens
multi-row merged headers, separates horizontal tables, tolerates one blank data
row, and excludes conventional total rows from records.
