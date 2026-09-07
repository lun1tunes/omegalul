"""Schedule Builder FastAPI: keyword object model, build/apply/diff and the agent-tools session API.

The agent itself is the n8n workflow ``Agent — Schedule Builder`` (LLM + these tools); there is no
Python ``/agent/run`` duplicate any more — one source of behaviour.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .apply import apply_operations
from .diff import unified_diff
from .emit import emit_schedule
from .keywords import KEYWORDS, all_keywords, keyword_object, search_keywords
from .schema_models import IREvent
from .schema_renderer import validate_and_render
from .schema_store import load_catalogue
from .parse import parse_schedule
from .validate import validate_emitted
from . import agent_tools
from . import sessions

app = FastAPI(title="schedule-builder-service", version="0.1.0")
ACTIVITY = os.getenv("ACTIVITY_BASE_URL", "").rstrip("/")
UNITS = "METRIC"


class BuildRequest(BaseModel):
    source_text: str = ""
    operations: list[dict[str, Any]] = Field(default_factory=list)
    units: str = UNITS


class ApplyRequest(BaseModel):
    source_text: str
    operations: list[dict[str, Any]] = Field(default_factory=list)


class DiffRequest(BaseModel):
    before: str
    after: str


class RenderIRRequest(BaseModel):
    mode: str = "CREATE"
    schema_catalogue: dict[str, Any] | None = None
    ir_events: list[dict[str, Any]] = Field(default_factory=list)


class AgentTaskBody(BaseModel):
    case_id: str = ""
    task_id: str = ""
    agent_id: str = "schedule_builder"
    objective: str = ""
    handoff_message: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "schedule-builder-service", "units": UNITS, "keywords": str(len(KEYWORDS))}


@app.get("/keywords")
def get_keywords() -> dict[str, Any]:
    return {"keywords": all_keywords(), "units": UNITS}


@app.get("/keywords/search")
def get_search(intent: str = "") -> dict[str, Any]:
    return {"keywords": search_keywords(intent)}


@app.get("/keywords/{keyword}")
def get_keyword(keyword: str) -> dict[str, Any]:
    item = keyword_object(keyword)
    if item is None:
        raise HTTPException(status_code=404, detail="unknown keyword")
    return item


@app.post("/render")
def render_ir(req: RenderIRRequest) -> dict[str, Any]:
    catalogue = req.schema_catalogue if isinstance(req.schema_catalogue, dict) else None
    return validate_and_render(
        mode=req.mode,
        schema_catalogue=catalogue or load_catalogue(),
        ir_events=[IREvent.model_validate(item) for item in req.ir_events],
    )


@app.post("/keywords/{keyword}/prepare")
def prepare_keyword(keyword: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    item = keyword_object(keyword)
    if item is None:
        raise HTTPException(status_code=404, detail="unknown keyword")
    return {"keyword": item, "draft": body or {}}


def _build(source_text: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
    doc = parse_schedule(source_text)
    applied, findings = apply_operations(doc, operations)
    text = emit_schedule(applied)
    findings.extend(validate_emitted(text, applied))
    hard = [f for f in findings if f.get("severity") == "error"]
    return {
        "schedule_text": text,
        "diff": unified_diff(source_text, text),
        "findings": findings,
        "changed_keywords": sorted({str(op.get("keyword") or "").upper() for op in operations if op.get("keyword")}),
        "ok": not hard,
    }


@app.post("/build")
def build(req: BuildRequest) -> dict[str, Any]:
    if req.units and req.units.upper() != UNITS:
        raise HTTPException(status_code=400, detail="units must be METRIC")
    return _build(req.source_text, req.operations)


@app.post("/apply")
def apply(req: ApplyRequest) -> dict[str, Any]:
    return _build(req.source_text, req.operations)


@app.post("/diff")
def diff(req: DiffRequest) -> dict[str, Any]:
    return {"diff": unified_diff(req.before, req.after)}


class AgentToolBody(BaseModel):
    model_config = {"extra": "allow"}
    session_id: str = ""


@app.post("/agent-tools/open_session")
def open_session(body: AgentTaskBody) -> dict[str, Any]:
    return agent_tools.open_session(body.model_dump(), activity=ACTIVITY)


@app.post("/agent-tools/{tool_name}")
def call_agent_tool(tool_name: str, body: AgentToolBody) -> dict[str, Any]:
    payload = body.model_dump()
    session_id = str(payload.pop("session_id", "") or "")
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required")
    try:
        return agent_tools.execute_tool(session_id, tool_name, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/sessions/{session_id}/result")
def get_session_result(session_id: str) -> dict[str, Any]:
    try:
        return agent_tools.session_result(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session_not_found") from exc


@app.post("/sessions/{session_id}/close")
def close_session(session_id: str) -> dict[str, Any]:
    return sessions.close(session_id)
