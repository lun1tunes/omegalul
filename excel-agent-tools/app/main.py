"""HTTP API for the Excel tools service; intentionally contains no LLM calls."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from dotenv import load_dotenv

# Native Windows/CMD launches often invoke Uvicorn directly instead of using
# Docker or the bundled .bat launcher.  Load service-local configuration before
# any module-level settings are evaluated.  A real process environment always
# wins; ``excel-tools.env`` is preferred while ``.env`` remains compatible with
# older deployments of this project.
SERVICE_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(SERVICE_ROOT / "excel-tools.env", override=False)
load_dotenv(SERVICE_ROOT / ".env", override=False)

from .sessions import cleanup_expired_sessions, init_state, load_state, locked_session, new_session_id, session_dir, session_file
from .tools import TOOL_SCHEMAS, execute_tool

# Import modules for registration. These modules never invoke an LLM.
from . import excel_tools as _excel_tools  # noqa: F401
from . import state_tools as _state_tools  # noqa: F401
from .agent_run import run_excel_agent

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
DOCS_ENABLED = os.getenv("EXCEL_TOOLS_ENABLE_DOCS", "false").strip().casefold() == "true"
app = FastAPI(
    title="Excel Tools Service",
    version="1.0.0",
    docs_url="/docs" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
    redoc_url=None,
)

API_KEY = os.getenv("API_KEY", "")
try:
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "200")) * 1024 * 1024
except ValueError:
    MAX_FILE_SIZE = 200 * 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}
try:
    MAX_ZIP_ENTRIES = int(os.getenv("MAX_EXCEL_ZIP_ENTRIES", "10000"))
    MAX_ZIP_UNCOMPRESSED_SIZE = int(os.getenv("MAX_EXCEL_UNCOMPRESSED_MB", "500")) * 1024 * 1024
except ValueError:
    MAX_ZIP_ENTRIES = 10000
    MAX_ZIP_UNCOMPRESSED_SIZE = 500 * 1024 * 1024


def _require_runtime_configuration() -> None:
    """Fail fast rather than exposing an unauthenticated tools API by mistake."""
    if not API_KEY:
        raise RuntimeError("API_KEY must be configured")
    if MAX_ZIP_ENTRIES < 1 or MAX_ZIP_UNCOMPRESSED_SIZE < 1:
        raise RuntimeError("Excel ZIP safety limits must be positive")


_require_runtime_configuration()


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if not isinstance(x_api_key, str) or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def _validate_excel_archive(path: Path, suffix: str) -> None:
    """Reject malformed and zip-bomb-like OOXML uploads before workbook parsing."""
    if suffix == ".xls":
        try:
            with path.open("rb") as file:
                signature = file.read(8)
            if signature != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Invalid Excel workbook")
        except OSError as error:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Invalid Excel workbook") from error
        return
    if suffix not in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        return
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise HTTPException(status_code=413, detail="Excel archive has too many entries")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Encrypted Excel workbooks are not supported")
            uncompressed_size = sum(entry.file_size for entry in entries)
            if uncompressed_size > MAX_ZIP_UNCOMPRESSED_SIZE:
                raise HTTPException(status_code=413, detail="Excel archive expands beyond the configured limit")
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Invalid Excel workbook")
    except HTTPException:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Invalid Excel workbook") from error


def safe_state(state: dict[str, Any]) -> dict[str, Any]:
    result = dict(state)
    result.pop("file_path", None)
    result.pop("tool_cache", None)
    for collection in ("result_sets", "artifacts"):
        result[collection] = {key: {field: value for field, value in item.items() if field != "path"} for key, item in result.get(collection, {}).items()}
    return result


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/v1/tools", dependencies=[Depends(require_api_key)])
def get_tools() -> dict[str, Any]:
    return {"tools": TOOL_SCHEMAS}


@app.post("/api/v1/sessions", dependencies=[Depends(require_api_key)], status_code=status.HTTP_201_CREATED)
async def create_session(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    payload: str = Form("{}"),
) -> dict[str, Any]:
    filename = file.filename or "input.xlsx"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Only Excel .xlsx, .xlsm, .xltx, .xltm and .xls files are accepted")
    try:
        payload_json = json.loads(payload)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload JSON") from error
    if not isinstance(payload_json, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payload must be a JSON object")

    session_id = new_session_id()
    try:
        directory = session_dir(session_id, create=True)
    except FileExistsError:  # Extremely improbable UUID collision; client gets a safe retry response.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Could not allocate session; retry")
    relative_path = f"input{suffix}"
    file_path = session_file(session_id, relative_path)
    total_size = 0
    digest = hashlib.sha256()
    try:
        with file_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File too large")
                destination.write(chunk)
                digest.update(chunk)
        _validate_excel_archive(file_path, suffix)
        state = init_state(session_id=session_id, file_path=relative_path, file_name=filename, file_hash=f"sha256:{digest.hexdigest()}", file_size=total_size, payload=payload_json)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()
    # Session cleanup scans every session directory. It must not make the upload
    # latency depend on the number of retained sessions.
    background_tasks.add_task(cleanup_expired_sessions)
    logger.info("Uploaded Excel session %s: %d bytes", session_id, total_size)
    return {"session_id": session_id, "status": "uploaded", "file_size": total_size, "file_hash": state["file_hash"]}


class SingleToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)


class AgentToolRequest(BaseModel):
    """Adapter for n8n AI-tool request transports.

    n8n 2.30.8 AI Agent 3.1 and HTTP Request Tool 1.1 send model parameters as
    top-level HTTP fields. Older exports may expose ``input``/``args`` instead;
    keep those two unambiguous compatibility shapes at this narrow boundary
    while the public session API remains strict.

    Crucially, ``session_id`` is a field value supplied by the workflow
    expression, never a function parameter exposed to the model.  Any allowed
    extra fields are merely the arguments for the already route-fixed tool.
    """

    model_config = ConfigDict(extra="allow")
    session_id: str = Field(min_length=1, max_length=128)
    # Keep compatibility with workflow exports that use an explicit envelope.
    input: dict[str, Any] | None = None
    args: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_one_argument_object(self) -> "AgentToolRequest":
        if self.input is not None and self.args is not None:
            raise ValueError("Provide either input or args, not both")
        if (self.input is not None or self.args is not None) and self.model_extra:
            raise ValueError("Do not mix input/args envelope with top-level tool arguments")
        return self

    def tool_args(self) -> dict[str, Any]:
        if self.input is not None:
            return self.input
        if self.args is not None:
            return self.args
        # Pydantic places allow-listed unknown top-level fields here.  Their
        # actual schema/validation is still enforced by the named Excel tool.
        return dict(self.model_extra or {})


class ToolCallItem(SingleToolRequest):
    call_id: str = Field(min_length=1, max_length=256)


class BatchToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calls: list[ToolCallItem] = Field(min_length=1, max_length=30)


def get_loaded_state(session_id: str) -> dict[str, Any]:
    try:
        return load_state(session_id)
    except ValueError as error:
        detail = str(error)
        code = status.HTTP_404_NOT_FOUND if detail == "Session not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=detail) from error


def _n8n_json_sequence(value: Any) -> Any:
    """Turn n8n HTTP Tool 1.1's object-shaped JSON into a canonical array.

    The node's UI exposes JSON parameters, but its generated Zod schema accepts
    JSON objects rather than arrays. Delivery workflows therefore describe
    array fields as zero-based objects. Keep this compatibility quirk entirely
    at the n8n adapter boundary; regular FastAPI clients continue to use arrays.
    """
    if not isinstance(value, dict):
        return value
    if not value:
        return []
    keys = list(value)
    if not all(isinstance(key, str) and key.isdigit() for key in keys):
        return value
    indexes = sorted(int(key) for key in keys)
    if indexes != list(range(len(keys))):
        return value
    return [value[str(index)] for index in indexes]


def normalize_agent_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Accept a few unambiguous LLM transport aliases before strict tool validation.

    The public session endpoints intentionally remain strict. This adapter only
    protects the constrained, named agent-tool transport from common function
    calling vocabulary such as ``fields`` versus the API's canonical ``select``.
    It never invents an ID, a filter value, or a column name.
    """
    normalized = dict(args)

    if tool_name == "describe_table" and "table_id" not in normalized:
        candidate = normalized.get("verified_table")
        if isinstance(candidate, str):
            normalized["table_id"] = candidate
        elif isinstance(candidate, dict) and isinstance(candidate.get("table_id"), str):
            normalized["table_id"] = candidate["table_id"]

    if tool_name == "query_table":
        if "table_id" not in normalized and isinstance(normalized.get("table"), str):
            normalized["table_id"] = normalized["table"]
        if "select" not in normalized and isinstance(normalized.get("fields"), list):
            normalized["select"] = normalized["fields"]
        # Preserve omission of optional fields. Injecting ``None`` here makes
        # the strict tool validator treat an otherwise valid no-filter query
        # as ``filters: null`` instead of applying its default empty list.
        for field in ("select", "filters"):
            if field in normalized:
                normalized[field] = _n8n_json_sequence(normalized[field])
        filters = normalized.get("filters")
        if isinstance(filters, dict):
            canonical_filters: list[dict[str, Any]] = []
            for field, condition in filters.items():
                if not isinstance(field, str):
                    continue
                if isinstance(condition, dict):
                    canonical_filters.append({"field": field, **condition})
                else:
                    canonical_filters.append({"field": field, "operator": "eq", "value": condition})
            normalized["filters"] = canonical_filters

    if tool_name == "save_agent_plan":
        candidate = normalized.get("verified_table")
        table_id = candidate.get("table_id") if isinstance(candidate, dict) else candidate
        if "selected_table_ids" not in normalized and isinstance(table_id, str):
            normalized["selected_table_ids"] = [table_id]
        if "field_mapping" not in normalized and isinstance(normalized.get("mapping"), dict):
            normalized["field_mapping"] = normalized["mapping"]
        if "select" not in normalized and isinstance(normalized.get("fields"), list):
            normalized["select"] = normalized["fields"]
        if "plan" not in normalized and isinstance(table_id, str):
            normalized["plan"] = f"Selected verified table {table_id}"
        for field in ("selected_table_ids", "select", "filters", "assumptions", "warnings"):
            if field in normalized:
                normalized[field] = _n8n_json_sequence(normalized[field])
        filters = normalized.get("filters")
        if isinstance(filters, dict):
            normalized["filters"] = [
                {"field": field, **condition} if isinstance(condition, dict) else {"field": field, "operator": "eq", "value": condition}
                for field, condition in filters.items()
                if isinstance(field, str)
            ]

    return normalized


@app.post("/api/v1/sessions/{session_id}/tool", dependencies=[Depends(require_api_key)])
def call_tool(session_id: str, body: SingleToolRequest) -> dict[str, Any]:
    try:
        with locked_session(session_id):
            state = get_loaded_state(session_id)
            return execute_tool(state, body.name, body.args)
    except ValueError as error:
        detail = str(error)
        code = status.HTTP_404_NOT_FOUND if detail == "Session not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=detail) from error


@app.post("/api/v1/agent-tools/{tool_name}", dependencies=[Depends(require_api_key)])
def call_agent_tool(tool_name: str, body: AgentToolRequest) -> dict[str, Any]:
    """Execute a named Excel tool through the single-object agent contract.

    ``tool_name`` is fixed by the orchestrator node, so the model cannot select
    an arbitrary endpoint.  The session remains authoritative and is protected
    by the same per-session advisory file lock as all other mutation endpoints.
    """
    try:
        with locked_session(body.session_id):
            state = get_loaded_state(body.session_id)
            return execute_tool(state, tool_name, normalize_agent_tool_args(tool_name, body.tool_args()))
    except ValueError as error:
        detail = str(error)
        code = status.HTTP_404_NOT_FOUND if detail == "Session not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=detail) from error


@app.post("/api/v1/sessions/{session_id}/tools/batch", dependencies=[Depends(require_api_key)])
def call_tools_batch(session_id: str, body: BatchToolRequest) -> dict[str, Any]:
    try:
        with locked_session(session_id):
            state = get_loaded_state(session_id)
            results: list[dict[str, Any]] = []
            for call in body.calls:
                result = execute_tool(state, call.name, call.args)
                results.append({"call_id": call.call_id, "name": call.name, **result})
            return {"session_id": session_id, "results": results}
    except ValueError as error:
        detail = str(error)
        code = status.HTTP_404_NOT_FOUND if detail == "Session not found" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=code, detail=detail) from error


@app.get("/api/v1/sessions/{session_id}/state", dependencies=[Depends(require_api_key)])
def get_session_state_endpoint(session_id: str) -> dict[str, Any]:
    return safe_state(get_loaded_state(session_id))


@app.get("/api/v1/artifacts/{session_id}/{artifact_id}", dependencies=[Depends(require_api_key)])
def download_artifact(session_id: str, artifact_id: str) -> FileResponse:
    state = get_loaded_state(session_id)
    artifact = state.get("artifacts", {}).get(artifact_id)
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    try:
        path = session_file(session_id, artifact["path"])
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found") from error
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file not found")
    media_type = "text/csv; charset=utf-8" if artifact.get("format") == "csv" else "application/x-ndjson"
    return FileResponse(path, media_type=media_type, filename=artifact.get("file_name", path.name))


class AgentTaskBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    case_id: str = ""
    task_id: str = ""
    agent_id: str = "excel_extractor"
    objective: str = ""
    handoff_message: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


@app.post("/agent/run", dependencies=[Depends(require_api_key)])
def agent_run(body: AgentTaskBody) -> dict[str, Any]:
    return run_excel_agent(body.model_dump())
