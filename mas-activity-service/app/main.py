"""MAS Activity service entrypoint.

Live API (Activity UI and n8n Orchestrator — MAS):
  * ``app.cases_api`` — ``/cases*`` (Postgres via Control Plane Proxy)
  * ``/health``, ``/ready``, ``/v1/diagnostics/connectivity``
  * ``/v1/knowledge/*`` (RAG cards editor), static pages ``/``, ``/knowledge``, ``/registry``, ``/t/{task_id}``
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import control_plane
from app import knowledge as knowledge_store
from app.cases_api import router as cases_router
from app.models import KnowledgeDocumentCreate, KnowledgeDocumentPatch, TASK_ID_RE
from app.orchestrator import hitl_backend, orchestrator_config_summary, probe_orchestrator_connectivity
from app.readiness import probe_n8n_stack
from app.settings import STATIC, VERSION, configure_logging, get_settings

configure_logging()
logger = logging.getLogger("mas-activity")

MAX_BODY_BYTES = 256_000
_started_at = datetime.now(timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_store() -> None:
    """Test helper — clears in-memory control-plane and case-watch state."""
    control_plane.reset_memory()
    from app import case_watch

    case_watch.reset()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info(
        "control plane required=%s configured=%s url=%s",
        settings.control_plane_required,
        control_plane.configured(),
        bool(settings.control_plane_proxy_url.strip()),
    )
    if settings.control_plane_required and not control_plane.configured():
        raise RuntimeError(
            "CONTROL_PLANE_PROXY_URL is required. "
            "Set it to the active n8n /webhook/mas-control-plane endpoint."
        )
    try:
        schema = control_plane.ensure_schema()
        logger.info("control plane schema %s", schema)
    except Exception:
        logger.exception("control plane schema ensure failed")
        raise
    yield


app = FastAPI(
    title="MAS Activity Service",
    version=VERSION,
    description="Live handoff transcript + HITL for Petroleum Engineering MAS.",
    lifespan=lifespan,
)

# Last add_middleware so CORS wraps errors/SSE. Do not set allow_credentials=True with "*".
# If the browser blocks: see README «CORS». To lock down, replace "*" with exact UI origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.include_router(cases_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    started = time.perf_counter()
    if request.method in {"POST", "PUT", "PATCH"} and request.url.path.startswith("/v1/"):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "%s %s crashed after %.0fms",
            request.method,
            request.url.path,
            (time.perf_counter() - started) * 1000,
        )
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    if request.url.path.startswith("/v1/") or request.url.path in {"/health", "/ready"}:
        logger.info("%s %s %s %.0fms", request.method, request.url.path, response.status_code, elapsed_ms)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/v1/") else response.headers.get("Cache-Control", "")
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "mas-activity",
        "version": VERSION,
        "mas_version": VERSION,
        "n8n_transport": settings.n8n_transport,
        "control_plane_backend": "n8n_proxy" if control_plane.configured() else "memory",
        "control_plane_required": settings.control_plane_required,
        "control_plane_proxy_configured": control_plane.configured(),
        "state_persist": bool(settings.activity_state_path.strip()),
        "time": _now(),
        "started_at": _started_at,
        "config": settings.public_summary(),
    }


@app.get("/ready")
async def ready() -> dict[str, Any]:
    report = await probe_n8n_stack()
    if not report.get("ready"):
        raise HTTPException(status_code=503, detail=report)
    return report


@app.get("/v1/diagnostics/connectivity")
async def diagnostics_connectivity() -> dict[str, Any]:
    """Check Activity → Orchestrator and extra n8n webhooks. No secrets are returned."""
    orchestrator, stack = await asyncio.gather(
        probe_orchestrator_connectivity(),
        probe_n8n_stack(),
    )
    ok = bool(stack.get("ready"))
    return {
        "status": "ok" if ok else "degraded",
        "ready": ok,
        "data_tables": {"configured": False, "note": "retired; cases live in Postgres"},
        "orchestrator": orchestrator,
        "n8n": stack,
        "config": {
            "n8n_transport": hitl_backend(),
            "orchestrator": orchestrator_config_summary(),
        },
        "note": "n8n /healthz, orchestrator, and extra webhook paths.",
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/knowledge")
def knowledge_page() -> FileResponse:
    return FileResponse(STATIC / "knowledge.html")


@app.get("/registry")
def agents_page() -> FileResponse:
    """Agents page: the ``agent_registry`` over ``GET/PUT /agents`` (bind imported workflows, edit when_to_use)."""
    return FileResponse(STATIC / "agents.html")


@app.get("/t/{task_id}")
def task_page(task_id: str) -> FileResponse:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    return FileResponse(STATIC / "index.html")


@app.get("/v1/knowledge/namespaces")
def knowledge_namespaces() -> dict[str, Any]:
    try:
        return {
            "contract": "mas_knowledge_namespaces",
            "contract_version": "1.0",
            "corpus_path": str(knowledge_store.corpus_path()),
            "namespaces": knowledge_store.list_namespaces(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/v1/knowledge/documents")
def knowledge_documents(target_base: str = Query(..., min_length=1, max_length=120)) -> dict[str, Any]:
    try:
        docs = knowledge_store.list_documents(target_base)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "contract": "mas_knowledge_document_list",
        "contract_version": "1.0",
        "target_base": target_base,
        "count": len(docs),
        "documents": docs,
    }


@app.get("/v1/knowledge/documents/{target_base}/{knowledge_id}")
def knowledge_document(target_base: str, knowledge_id: str) -> dict[str, Any]:
    try:
        doc = knowledge_store.get_document(target_base, knowledge_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "contract": "mas_knowledge_document",
        "contract_version": "1.0",
        "document": doc,
    }


@app.post("/v1/knowledge/documents")
def knowledge_create_document(
    body: KnowledgeDocumentCreate,
) -> dict[str, Any]:
    try:
        doc = knowledge_store.create_document(
            target_base=body.target_base,
            knowledge_id=body.knowledge_id,
            knowledge_type=body.knowledge_type,
            title=body.title,
            text=body.text,
            keywords=body.keywords,
            topics=body.topics,
            task_patterns=body.task_patterns,
            author=body.author,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "contract": "mas_knowledge_document",
        "contract_version": "1.0",
        "status": "created",
        "document": doc,
    }


@app.patch("/v1/knowledge/documents/{target_base}/{knowledge_id}")
def knowledge_patch_document(
    target_base: str,
    knowledge_id: str,
    body: KnowledgeDocumentPatch,
) -> dict[str, Any]:
    try:
        doc = knowledge_store.patch_document(
            target_base,
            knowledge_id,
            text=body.text,
            title=body.title,
            keywords=body.keywords,
            topics=body.topics,
            task_patterns=body.task_patterns,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "contract": "mas_knowledge_document",
        "contract_version": "1.0",
        "status": "saved",
        "document": doc,
    }


@app.post("/v1/knowledge/ingest")
async def knowledge_ingest() -> dict[str, Any]:
    try:
        result = await knowledge_store.push_corpus_to_n8n()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except knowledge_store.KnowledgeIngestUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "contract": "mas_knowledge_ingest_result",
        "contract_version": "1.0",
        **result,
    }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
