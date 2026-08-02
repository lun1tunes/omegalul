"""HTTP API for the Excel tools service; intentionally contains no LLM calls."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .sessions import cleanup_expired_sessions, init_state, load_state, new_session_id, session_dir, session_file
from .tools import TOOL_SCHEMAS, execute_tool

# Import modules for registration. These modules never invoke an LLM.
from . import excel_tools as _excel_tools  # noqa: F401
from . import state_tools as _state_tools  # noqa: F401

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
app = FastAPI(title="Excel Tools Service", version="1.0.0", docs_url="/docs", redoc_url=None)

API_KEY = os.getenv("API_KEY", "")
try:
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_MB", "200")) * 1024 * 1024
except ValueError:
    MAX_FILE_SIZE = 200 * 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"}


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    # An empty API_KEY is allowed only for local development; production compose requires it.
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def safe_state(state: dict[str, Any]) -> dict[str, Any]:
    result = dict(state)
    result.pop("file_path", None)
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
async def create_session(file: UploadFile = File(...), payload: str = Form("{}")) -> dict[str, Any]:
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

    cleanup_expired_sessions()
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
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")
                destination.write(chunk)
                digest.update(chunk)
        state = init_state(session_id=session_id, file_path=relative_path, file_name=filename, file_hash=f"sha256:{digest.hexdigest()}", file_size=total_size, payload=payload_json)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()
    logger.info("Uploaded Excel session %s: %d bytes", session_id, total_size)
    return {"session_id": session_id, "status": "uploaded", "file_size": total_size, "file_hash": state["file_hash"]}


class SingleToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)


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


@app.post("/api/v1/sessions/{session_id}/tool", dependencies=[Depends(require_api_key)])
def call_tool(session_id: str, body: SingleToolRequest) -> dict[str, Any]:
    state = get_loaded_state(session_id)
    return execute_tool(state, body.name, body.args)


@app.post("/api/v1/sessions/{session_id}/tools/batch", dependencies=[Depends(require_api_key)])
def call_tools_batch(session_id: str, body: BatchToolRequest) -> dict[str, Any]:
    state = get_loaded_state(session_id)
    results: list[dict[str, Any]] = []
    for call in body.calls:
        result = execute_tool(state, call.name, call.args)
        results.append({"call_id": call.call_id, "name": call.name, **result})
    return {"session_id": session_id, "results": results}


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
