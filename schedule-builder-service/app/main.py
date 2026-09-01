"""Schedule Builder FastAPI: keyword object model + build/apply/diff + AgentTask."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .apply import apply_operations
from .commissioning import run_commissioning_revise
from .diff import unified_diff
from .emit import emit_schedule
from .group_rebind import extract_group_rebind_spec, run_group_rebind_revise, wants_group_rebind
from .io import commissioning_facts, file_ref, load_source
from .keywords import KEYWORDS, all_keywords, keyword_object, search_keywords
from .parse import parse_schedule, well_names
from .validate import validate_emitted
from . import agent_tools

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


class AgentTaskBody(BaseModel):
    case_id: str = ""
    task_id: str = ""
    agent_id: str = "schedule_builder"
    objective: str = ""
    handoff_message: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


def _emit(case_id: str, activity: str, payload: dict[str, Any]) -> None:
    if not case_id or not activity:
        return
    try:
        req = Request(
            f"{activity.rstrip('/')}/cases/{case_id}/events",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=8).read()
    except Exception:
        pass


def _load_source(inputs: dict[str, Any], case_id: str) -> str:
    return load_source(inputs, case_id, ACTIVITY)


def _ops_from_excel(context: dict[str, Any], source_wells: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    excel = (context.get("data") or {}).get("excel") if isinstance(context.get("data"), dict) else context.get("excel")
    operations: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    tables = []
    if isinstance(excel, dict):
        tables = excel.get("normalized_rows") or []
    for table in tables:
        preview = table.get("preview") if isinstance(table, dict) else []
        columns = [str(c).lower() for c in (table.get("columns") or [])] if isinstance(table, dict) else []
        if any("дат" in col or "date" in col or "ввод" in col for col in columns):
            continue
        for index, row in enumerate(preview or []):
            if not isinstance(row, dict):
                continue
            well = str(row.get("well") or row.get("WELL") or row.get("скважина") or "").strip()
            if not well:
                continue
            if source_wells and well not in source_wells:
                missing.append({"type": "well_not_found", "well": well, "source_row": index})
                continue
            fields = {"well": well}
            for key in ("status", "ORAT", "WRAT", "BHP", "orat", "wrat", "bhp"):
                if key in row and row[key] not in (None, ""):
                    fields[key.upper() if key in {"orat", "wrat", "bhp"} else key] = row[key]
            if len(fields) > 1:
                operations.append({"keyword": "WCONPROD", "operation": "MODIFY", "fields": fields})
    return operations, missing


def _wants_commissioning(body: AgentTaskBody, facts: list[dict[str, Any]]) -> bool:
    blob = " ".join(
        [
            body.objective or "",
            body.handoff_message or "",
        ]
    ).lower()
    if any(token in blob for token in ("дат ввод", "даты ввод", "дата ввод", "commissioning", "ввод скваж")):
        return True
    caps = body.inputs.get("requested_capability_scope") if isinstance(body.inputs, dict) else None
    if isinstance(caps, list) and any("commission" in str(item).lower() or "date_retarget" in str(item).lower() for item in caps):
        return True
    return bool(facts)


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


@app.post("/agent/run")
def agent_run(body: AgentTaskBody) -> dict[str, Any]:
    inputs = body.inputs if isinstance(body.inputs, dict) else {}
    activity = str(inputs.get("activity_base_url") or ACTIVITY)
    _emit(
        body.case_id,
        activity,
        {
            "kind": "agent.accepted",
            "actor": "schedule_builder",
            "agent_id": "schedule_builder",
            "task_id": body.task_id,
            "status_message": "Формирую изменения SCHEDULE",
        },
    )
    try:
        source = _load_source(inputs, body.case_id)
    except Exception as exc:
        return {
            "task_id": body.task_id,
            "status": "needs_input",
            "message": f"Нет исходного SCHEDULE: {exc}",
            "data": {},
            "artifacts": {},
            "issues": [{"type": "missing_schedule_source"}],
            "assumptions": [],
            "requests": [{"question_id": "Q-sched", "question": "Приложите baseline .inc", "options": []}],
        }
    if not source.strip():
        result = {
            "task_id": body.task_id,
            "status": "needs_input",
            "message": "Нет исходного SCHEDULE",
            "data": {},
            "artifacts": {},
            "issues": [{"type": "missing_schedule_source"}],
            "assumptions": [],
            "requests": [{"question_id": "Q-sched", "question": "Приложите baseline .inc", "options": []}],
        }
        _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "needs_input", "status_message": result["message"]})
        return result
    context = body.context if isinstance(body.context, dict) else {}
    source_name = file_ref(inputs)
    blob = body.objective or ""
    if wants_group_rebind(blob, inputs):
        wells = well_names(parse_schedule(source))
        spec, missing = extract_group_rebind_spec(
            blob,
            wells,
            source_text=source,
            hitl=context.get("hitl") if isinstance(context.get("hitl"), dict) else {},
            inputs=inputs,
        )
        if missing:
            result = {
                "task_id": body.task_id,
                "status": "needs_input",
                "message": "Для перепривязки групп нужны: " + ", ".join(missing),
                "data": {"group_rebind": spec, "missing": missing},
                "artifacts": {},
                "issues": [{"type": "GROUP_REBIND_SPEC_REQUIRED", "missing": missing}],
                "assumptions": [{"units": UNITS}],
                "requests": [
                    {
                        "question_id": item,
                        "question": f"Уточните {item} для перепривязки групп (скважины, parent_group, parent_of_parent, well_groups, control, rate).",
                        "options": [],
                    }
                    for item in missing
                ],
            }
            _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "needs_input", "status_message": result["message"]})
            return result
        _emit(
            body.case_id,
            activity,
            {
                "kind": "agent.progress",
                "actor": "schedule_builder",
                "agent_id": "schedule_builder",
                "task_id": body.task_id,
                "status_message": f"Перепривязываю скважины {', '.join(spec['wells'])} в группу {spec['parent_group']}",
            },
        )
        try:
            revised = run_group_rebind_revise(source, spec, file_ref=source_name)
        except Exception as exc:
            result = {
                "task_id": body.task_id,
                "status": "failed",
                "message": f"Не удалось перепривязать группы: {exc}"[:400],
                "data": {},
                "artifacts": {},
                "issues": [{"type": "group_rebind_failed", "detail": str(exc)[:400]}],
                "assumptions": [{"units": UNITS}],
                "requests": [],
            }
            _emit(body.case_id, activity, {"kind": "agent.failed", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "failed", "status_message": result["message"]})
            return result
        status = str(revised.get("status") or "")
        text = str(revised.get("generated_schedule") or "")
        if status == "needs_input" or not text.strip():
            result = {
                "task_id": body.task_id,
                "status": "needs_input",
                "message": "Перепривязка групп требует уточнения по baseline",
                "data": {"findings": revised.get("findings") or [], "group_rebind": spec},
                "artifacts": {},
                "issues": revised.get("findings") or [],
                "assumptions": [{"units": UNITS}],
                "requests": [{"question_id": "group_rebind", "question": "Уточните spec перепривязки групп", "options": []}],
            }
            _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "needs_input", "status_message": result["message"]})
            return result
        result = {
            "task_id": body.task_id,
            "status": "completed",
            "message": f"Перепривязал {len(spec['wells'])} скважин в {spec['parent_group']} (GCONPROD {spec['control']} {spec['gas_rate']})",
            "data": {
                "changed_keywords": ["WELSPECS", "GRUPTREE", "GCONPROD"],
                "findings": revised.get("findings") or [],
                "edits": revised.get("edits") or [],
                "group_rebind": spec,
            },
            "artifacts": {"schedule_out": text, "diff": unified_diff(source, text)},
            "issues": revised.get("findings") or [],
            "assumptions": [
                {"units": UNITS, "well_groups": spec.get("well_groups"), "parent_of_parent": spec.get("parent_of_parent")}
            ],
            "requests": [],
        }
        _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "completed", "status_message": result["message"]})
        return result
    facts = commissioning_facts(context, inputs)
    if _wants_commissioning(body, facts):
        if not facts:
            result = {
                "task_id": body.task_id,
                "status": "needs_input",
                "message": "Для сдвига дат ввода нужны факты скважина + дата из Excel",
                "data": {},
                "artifacts": {},
                "issues": [{"type": "commissioning_facts_required"}],
                "assumptions": [],
                "requests": [{"question_id": "Q-facts", "question": "Приложите Excel со скважинами и датами ввода", "options": []}],
            }
            _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "needs_input", "status_message": result["message"]})
            return result
        _emit(
            body.case_id,
            activity,
            {
                "kind": "agent.progress",
                "actor": "schedule_builder",
                "agent_id": "schedule_builder",
                "task_id": body.task_id,
                "status_message": f"Заменяю даты ввода скважин в исходном файле schedule ({len(facts)} скважин)",
            },
        )
        try:
            revised = run_commissioning_revise(
                source,
                facts,
                file_ref=source_name,
                unlisted_wells_policy=agent_tools._unlisted_policy(
                    {"inputs": inputs, "context": context}
                )
                or str(inputs.get("unlisted_wells_policy") or "")
                or None,
                new_well_defs=agent_tools._new_well_defs({"inputs": inputs, "context": context}),
                instruction_blob=" ".join([body.objective or "", body.handoff_message or ""]),
            )
        except Exception as exc:
            result = {
                "task_id": body.task_id,
                "status": "failed",
                "message": f"Не удалось сдвинуть даты ввода: {exc}"[:400],
                "data": {},
                "artifacts": {},
                "issues": [{"type": "commissioning_failed", "detail": str(exc)[:400]}],
                "assumptions": [{"units": UNITS}],
                "requests": [],
            }
            _emit(body.case_id, activity, {"kind": "agent.failed", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "failed", "status_message": result["message"]})
            return result
        status = str(revised.get("status") or "")
        if status == "needs_input":
            questions = revised.get("questions") or []
            result = {
                "task_id": body.task_id,
                "status": "needs_input",
                "message": (questions[0].get("question") if questions and isinstance(questions[0], dict) else "Нужно уточнение по скважинам вне Excel"),
                "data": {"findings": revised.get("findings") or [], "unlisted_wells": revised.get("unlisted_wells") or []},
                "artifacts": {},
                "issues": revised.get("findings") or [],
                "assumptions": [{"units": UNITS}],
                "requests": questions if isinstance(questions, list) else [],
            }
            _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "needs_input", "status_message": result["message"]})
            return result
        text = str(revised.get("generated_schedule") or "")
        result = {
            "task_id": body.task_id,
            "status": "completed" if text.strip() and status in {"applied", "noop"} else "failed",
            "message": (
                f"Сдвинул даты ввода для {len(revised.get('shifts') or [])} скважин"
                if status == "applied"
                else "SCHEDULE без изменений"
            ),
            "data": {
                "changed_keywords": ["DATES", "WCONPROD"],
                "findings": revised.get("findings") or [],
                "edits": revised.get("edits") or [],
                "shifts": revised.get("shifts") or [],
                "records_applied": len(revised.get("edits") or []),
            },
            "artifacts": {"schedule_out": text, "diff": unified_diff(source, text)},
            "issues": revised.get("findings") or [],
            "assumptions": [{"units": UNITS, "unlisted_wells_policy": revised.get("unlisted_wells_policy") or "keep"}],
            "requests": [],
        }
        _emit(
            body.case_id,
            activity,
            {
                "kind": "agent.result" if result["status"] == "completed" else "agent.failed",
                "actor": "schedule_builder",
                "agent_id": "schedule_builder",
                "task_id": body.task_id,
                "status": result["status"],
                "status_message": result["message"],
            },
        )
        return result
    doc = parse_schedule(source)
    wells = well_names(doc)
    operations = list(inputs.get("operations") or [])
    extra, missing = _ops_from_excel(context, wells)
    operations.extend(extra)
    if missing:
        result = {
            "task_id": body.task_id,
            "status": "needs_input",
            "message": f"Скважина '{missing[0]['well']}' есть в Excel, но не найдена в SCHEDULE",
            "data": {},
            "artifacts": {},
            "issues": missing,
            "assumptions": [],
            "requests": [
                {
                    "question_id": "Q-well",
                    "question": f"Скважина '{missing[0]['well']}' не найдена в SCHEDULE. Пропустить или добавить новую?",
                    "options": ["skip", "add_new"],
                }
            ],
        }
        _emit(body.case_id, activity, {"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder", "task_id": body.task_id, "status": "needs_input", "status_message": result["message"]})
        return result
    built = _build(source, operations)
    result = {
        "task_id": body.task_id,
        "status": "completed" if built["ok"] else "failed",
        "message": "SCHEDULE обновлён" if built["ok"] else "Ошибка сборки SCHEDULE",
        "data": {
            "changed_keywords": built["changed_keywords"],
            "findings": built["findings"],
            "records_applied": len(operations),
        },
        "artifacts": {"schedule_out": built["schedule_text"], "diff": built["diff"]},
        "issues": built["findings"],
        "assumptions": [{"units": UNITS}],
        "requests": [],
    }
    _emit(
        body.case_id,
        activity,
        {
            "kind": "agent.result" if built["ok"] else "agent.failed",
            "actor": "schedule_builder",
            "agent_id": "schedule_builder",
            "task_id": body.task_id,
            "status": result["status"],
            "status_message": result["message"],
        },
    )
    return result


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
