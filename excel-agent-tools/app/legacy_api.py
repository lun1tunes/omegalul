"""Direct HTTP API of the Excel tools without n8n: upload a workbook, call tools, export artifacts.

Used by the retired workflows, by pytest and for debugging a workbook by hand. The MAS agent contract
(``/agent-tools/open_session``, ``/agent-tools/{tool}``, ``/sessions/{id}/result``) is served by
``mas_agent_kit.agent_router`` in ``main.py`` — nothing here is needed for a case to run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from mas_agent_kit import SessionNotFound
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import agent
from .sessions import cleanup_expired_sessions, init_state, load_state, locked_session, new_session_id, session_dir, session_file
from .tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

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
if MAX_ZIP_ENTRIES < 1 or MAX_ZIP_UNCOMPRESSED_SIZE < 1:
    raise RuntimeError("Excel ZIP safety limits must be positive")


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


def _session_http_error(error: Exception) -> HTTPException:
    """Unknown / expired session → 404; unreadable state or bad ids elsewhere → 422."""
    if isinstance(error, SessionNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))


def get_loaded_state(session_id: str) -> dict[str, Any]:
    try:
        return load_state(session_id)
    except (SessionNotFound, ValueError) as error:
        raise _session_http_error(error) from error


class SingleToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)


class AgentToolRequest(BaseModel):
    """Adapter for n8n AI-tool request transports.

    n8n 2.30.8 AI Agent 3.1 and HTTP Request Tool 1.1 send model parameters as top-level HTTP fields.
    Older exports may expose ``input``/``args`` instead; keep those two unambiguous compatibility shapes
    at this narrow boundary while the public session API remains strict.

    ``session_id`` is a field value supplied by the workflow expression, never a function parameter
    exposed to the model. Any allowed extra fields are merely the arguments for the route-fixed tool.
    """

    model_config = ConfigDict(extra="allow")
    session_id: str = Field(min_length=1, max_length=128)
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
        return dict(self.model_extra or {})


class ToolCallItem(SingleToolRequest):
    call_id: str = Field(min_length=1, max_length=256)


class BatchToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calls: list[ToolCallItem] = Field(min_length=1, max_length=30)


@router.get("/tools")
def get_tools() -> dict[str, Any]:
    return {"tools": TOOLS.schemas}


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(background_tasks: BackgroundTasks, file: UploadFile = File(...), payload: str = Form("{}")) -> dict[str, Any]:
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
    # Session cleanup scans every session directory; it must not add to the upload latency.
    background_tasks.add_task(cleanup_expired_sessions)
    logger.info("Uploaded Excel session %s: %d bytes", session_id, total_size)
    return {"session_id": session_id, "status": "uploaded", "file_size": total_size, "file_hash": state["file_hash"]}


@router.post("/sessions/{session_id}/tool")
def call_tool(session_id: str, body: SingleToolRequest) -> dict[str, Any]:
    try:
        with locked_session(session_id):
            state = get_loaded_state(session_id)
            return execute_tool(state, body.name, body.args)
    except (SessionNotFound, ValueError) as error:
        raise _session_http_error(error) from error


@router.post("/agent-tools/{tool_name}")
def call_agent_tool(tool_name: str, body: AgentToolRequest) -> dict[str, Any]:
    """The n8n-shaped tool call (``{session_id, …args}``) with the agent's transport aliases and feed lines."""
    try:
        with locked_session(body.session_id):
            state = get_loaded_state(body.session_id)
            result = execute_tool(state, tool_name, agent.normalize_args(tool_name, body.tool_args()))
        agent.after_tool(state, tool_name, result)
        return result
    except (SessionNotFound, ValueError) as error:
        raise _session_http_error(error) from error


@router.post("/sessions/{session_id}/tools/batch")
def call_tools_batch(session_id: str, body: BatchToolRequest) -> dict[str, Any]:
    try:
        with locked_session(session_id):
            state = get_loaded_state(session_id)
            results: list[dict[str, Any]] = []
            for call in body.calls:
                result = execute_tool(state, call.name, call.args)
                results.append({"call_id": call.call_id, "name": call.name, **result})
            return {"session_id": session_id, "results": results}
    except (SessionNotFound, ValueError) as error:
        raise _session_http_error(error) from error


@router.get("/sessions/{session_id}/state")
def get_session_state_endpoint(session_id: str) -> dict[str, Any]:
    return safe_state(get_loaded_state(session_id))


@router.get("/artifacts/{session_id}/{artifact_id}")
def download_artifact(session_id: str, artifact_id: str) -> FileResponse:
    state = get_loaded_state(session_id)
    artifact = state.get("artifacts", {}).get(artifact_id)
    if not artifact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    try:
        path = session_file(session_id, artifact["path"])
    except (KeyError, ValueError) as error:  # KeyError covers SessionNotFound
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found") from error
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file not found")
    media_type = "text/csv; charset=utf-8" if artifact.get("format") == "csv" else "application/x-ndjson"
    return FileResponse(path, media_type=media_type, filename=artifact.get("file_name", path.name))
