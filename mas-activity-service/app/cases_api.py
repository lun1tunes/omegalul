"""Case control-plane HTTP API (state, events, SSE, HITL, run)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app import control_plane
from app.contracts import AGENT_EVENT_KINDS, CaseAnswerIn, CaseEventIn, CaseNameIn, MAX_STEPS
from app.orchestrator import OrchestratorError, invoke_orchestrator
from app.schema_view import build_schema_model
from app.settings import UNCONFIGURED_N8N, get_settings
from app.task_binaries import load_task_binaries, save_task_binaries

logger = logging.getLogger("mas-activity.cases")
router = APIRouter()

EXCEL_EXT = (".xlsx", ".xls", ".xlsm", ".xltx", ".xltm")
TRAJ_EXT = (".dev",)
SURF_EXT = (".cps3", ".grd", ".grid", ".xyz", ".zmap")
SCHED_EXT = (".inc", ".data", ".sch", ".grdecl", ".txt")


ORCH = "orchestrator"
USER = "user"
TASK_NAME_MAX = 120
RESTARTABLE_STATUSES = frozenset({"done", "failed", "retryable_error"})
RESTART_ACTIONS = frozenset({"retry", "restart"})


def _normalize_task_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    return name[:TASK_NAME_MAX]


def case_is_restartable(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return str(row.get("status") or "") in RESTARTABLE_STATUSES


def event_lane(event: dict[str, Any]) -> tuple[str, str | None, str]:
    """One chat line: (left, right, dir). dir is out (→), in (←), or none."""
    kind = str(event.get("kind") or "")
    actor = str(event.get("actor") or "").strip() or ORCH
    agent = str(event.get("agent_id") or "").strip() or None
    if kind == "case.created":
        return USER, ORCH, "out"
    if kind == "hitl.request":
        return USER, ORCH, "in"
    if kind == "hitl.answered":
        return USER, ORCH, "out"
    if kind == "agent.handoff":
        return ORCH, agent or "agent", "out"
    if kind in {"agent.accepted", "agent.progress"}:
        return agent or actor, None, "none"
    if kind in {"agent.result", "agent.failed"}:
        return ORCH, agent or actor, "in"
    if kind.startswith("orchestrator.") or kind in {"case.finished", "case.failed", "system.node_error"}:
        return ORCH, None, "none"
    if agent and agent not in {actor, ORCH}:
        return actor, agent, "out"
    if actor != ORCH:
        return ORCH, actor, "none"
    return ORCH, None, "none"


ORCH_ECHO_KINDS = {"orchestrator.status", "orchestrator.decision"}
AGENT_DUP_KINDS = {"agent.result", "agent.failed", "agent.accepted", "agent.progress"}
TERMINAL_KINDS = {"case.finished", "case.failed"}


def _event_message(event: dict[str, Any]) -> str:
    return " ".join(str(event.get("status_message") or "").split())


def _same_agent_line(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_task = str(left.get("task_id") or "")
    right_task = str(right.get("task_id") or "")
    task_ok = left_task == right_task or not left_task or not right_task
    return (
        str(left.get("kind") or "") == str(right.get("kind") or "")
        and str(left.get("agent_id") or left.get("actor") or "")
        == str(right.get("agent_id") or right.get("actor") or "")
        and task_ok
        and _event_message(left) == _event_message(right)
    )


def collapse_duplicate_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Drop echo rows that the old orchestrator wrote as separate kinds.

    Agents already POST agent.result; n8n also INSERTed the same line.
    Parse decision wrote orchestrator.status + orchestrator.decision + case.finished
    with one status_message. Goldens keep the raw rows; the feed shows one of each.
    """
    out: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        msg = _event_message(event)
        if kind in AGENT_DUP_KINDS and out and _same_agent_line(out[-1], event):
            continue
        if kind in ORCH_ECHO_KINDS and out:
            prev = out[-1]
            if str(prev.get("kind") or "") in ORCH_ECHO_KINDS and _event_message(prev) == msg:
                if kind == "orchestrator.decision":
                    out[-1] = event
                continue
        if kind in TERMINAL_KINDS:
            while out and str(out[-1].get("kind") or "") in ORCH_ECHO_KINDS and _event_message(out[-1]) == msg:
                out.pop()
        out.append(event)
    return out


def event_to_turn(event: dict[str, Any]) -> dict[str, Any]:
    kind = str(event.get("kind") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    handoff = event.get("handoff_message")
    errorish = kind in {"agent.failed", "system.node_error", "case.failed"}
    left, right, lane_dir = event_lane(event)
    if errorish:
        turn_kind = "error"
    elif kind == "agent.handoff":
        turn_kind = "handoff"
    elif kind.startswith("agent."):
        turn_kind = "event"
    else:
        turn_kind = "status"
    return {
        "at": _iso(event.get("created_at")),
        "stage": kind,
        "status": kind,
        "summary": event.get("status_message") or kind,
        "text": event.get("status_message") or "",
        "brief": event.get("status_message") or "",
        "from": {"role": left},
        "to": {"role": right or left},
        "from_role": left,
        "to_role": right or left,
        "lane_dir": lane_dir,
        "event_type": kind,
        "handoff_message": handoff,
        "kind": turn_kind,
        "details": {
            "kind": kind,
            "agent_id": event.get("agent_id"),
            "handoff_message": handoff,
            "payload": payload,
            "event_id": event.get("event_id"),
            "lane_dir": lane_dir,
        },
        "chips": _chips(event),
        "outcome": "error" if errorish else "ok",
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _chips(event: dict[str, Any]) -> list[dict[str, Any]]:
    chips = []
    if event.get("agent_id"):
        chips.append({"id": "agent_id", "label": "Агент", "value": event["agent_id"]})
    if event.get("kind"):
        chips.append({"id": "kind", "label": "Событие", "value": event["kind"]})
    return chips


def case_rail_item(row: dict[str, Any]) -> dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    status = str(row.get("status") or state.get("status") or "")
    name = _normalize_task_name(state.get("task_name"))
    return {
        "task_id": row["case_id"],
        "case_id": row["case_id"],
        "task_name": name,
        "title": (name or state.get("goal") or row["case_id"])[:80],
        "updated_at": _iso(row.get("updated_at")),
        "status": status,
        "last_status": status,
        "turn_count": int(row.get("event_count") or 0),
        "awaiting_human": status == "waiting_user",
        "restartable": case_is_restartable({"status": status}),
    }


def _schedule_filename(artifacts: dict[str, Any]) -> str:
    src = artifacts.get("schedule_source")
    if isinstance(src, dict):
        name = str(src.get("filename") or "").strip()
        if name:
            return name
    if isinstance(src, str) and src.strip():
        return src.strip()
    return "schedule.inc"


def _schedule_out_text(artifacts: dict[str, Any]) -> str | None:
    text = artifacts.get("schedule_out")
    if isinstance(text, str) and len(text.strip()) > 20:
        return text
    if isinstance(text, dict):
        inner = text.get("text") or text.get("content")
        if isinstance(inner, str) and len(inner.strip()) > 20:
            return inner
    return None


def _schedule_artifact(case_id: str, artifacts: dict[str, Any]) -> dict[str, Any] | None:
    text = _schedule_out_text(artifacts)
    if not text:
        return None
    return {
        "available": True,
        "filename": _schedule_filename(artifacts),
        "byte_length": len(text.encode("utf-8")),
        "inline": True,
        "download_path": f"/cases/{case_id}/schedule",
    }


def case_feed(case_id: str, after_seq: int = 0) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    state = row["state"] if isinstance(row["state"], dict) else {}
    events = collapse_duplicate_events(control_plane.list_events(case_id, after_seq=after_seq))
    turns = [event_to_turn(event) for event in events]
    pending = state.get("hitl") if isinstance(state.get("hitl"), dict) else {}
    questions = pending.get("questions") if isinstance(pending.get("questions"), list) else []
    gate = None
    if row["status"] == "waiting_user" and questions:
        q0 = questions[0] if isinstance(questions[0], dict) else {}
        gate = {
            "gate_id": q0.get("question_id") or "hitl",
            "kind": "needs_input",
            "reason": q0.get("question") or state.get("goal") or "Нужен ответ",
            "questions": questions,
        }
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    attached = [
        str(item.get("filename") or key)
        for key, item in artifacts.items()
        if isinstance(item, dict)
    ]
    name = _normalize_task_name(state.get("task_name"))
    return {
        "ok": True,
        "task_id": case_id,
        "case_id": case_id,
        "task_name": name,
        "title": (name or state.get("goal") or case_id)[:120],
        "objective": state.get("goal") or "",
        "status": row["status"],
        "state": state,
        "attached_files": attached,
        "awaiting_human": row["status"] == "waiting_user",
        "human_gate": gate,
        "activity": turns,
        "events": events,
        "schema": build_schema_model(events, state=state, status=row["status"]),
        "after_seq": after_seq,
        "schedule_artifact": _schedule_artifact(case_id, artifacts),
        "restartable": case_is_restartable(row),
    }


async def _read_upload(upload: UploadFile, *, field: str) -> tuple[str, bytes, str]:
    filename = (upload.filename or field).strip()
    content = await upload.read()
    mime = upload.content_type or "application/octet-stream"
    return filename, content, mime


def _artifact_slot(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(EXCEL_EXT):
        return "excel"
    if lower.endswith(TRAJ_EXT):
        return "trajectory"
    if any(lower.endswith(ext) for ext in SURF_EXT):
        return "surface"
    if any(lower.endswith(ext) for ext in SCHED_EXT):
        return "schedule_source"
    return "attachment"


def _new_case_id() -> str:
    return f"CASE-{int(time.time()):x}-{secrets.token_hex(3)}"


async def _invoke_step(case_id: str) -> None:
    try:
        await invoke_orchestrator({"case_id": case_id, "action": "step"}, timeout_s=180.0)
    except OrchestratorError as exc:
        logger.error("orchestrator step failed case_id=%s: %s", case_id, exc)
        control_plane.append_event(
            case_id,
            kind="case.failed",
            actor="orchestrator",
            status="failed",
            status_message=str(exc)[:500],
            payload={"status_code": exc.status_code},
        )
        try:
            row = control_plane.get_case(case_id)
            if row:
                state = dict(row["state"] or {})
                state["last_error"] = {"message": str(exc)[:500]}
                control_plane.update_case(case_id, state=state, status="failed")
        except KeyError:
            pass


@router.get("/cases")
def list_cases() -> dict[str, Any]:
    rows = control_plane.list_cases()
    return {"tasks": [case_rail_item(row) for row in rows], "cases": [case_rail_item(row) for row in rows]}


@router.post("/cases")
async def create_case(
    background_tasks: BackgroundTasks,
    task_description: str = Form(...),
    task_name: str = Form(default=""),
    requested_by: str = Form(default="mas activity user"),
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    surface_file: UploadFile | None = File(default=None),
    schedule_files: list[UploadFile] | None = File(default=None),
    trajectory_files: list[UploadFile] | None = File(default=None),
    attachments: list[UploadFile] | None = File(default=None),
    schedule_root: str = Form(default=""),
) -> dict[str, Any]:
    goal = (task_description or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="task_description is required")
    if get_settings().n8n_transport == "unconfigured":
        raise HTTPException(status_code=503, detail=UNCONFIGURED_N8N)
    name = _normalize_task_name(task_name)
    case_id = _new_case_id()
    uploads: list[tuple[str, str, bytes, str]] = []

    async def take(upload: UploadFile | None, slot_hint: str | None = None) -> None:
        if upload is None or not (upload.filename or "").strip():
            return
        filename, content, mime = await _read_upload(upload, field=slot_hint or "file")
        slot = slot_hint or _artifact_slot(filename)
        uploads.append((slot, filename, content, mime))

    await take(file, "excel")
    await take(surface_file, "surface")
    for item in trajectory_files or []:
        await take(item, "trajectory")
    for item in schedule_files or []:
        await take(item, "schedule_source")
    for item in attachments or []:
        await take(item, None)
    for item in files or []:
        await take(item, None)

    binary = {}
    artifacts: dict[str, Any] = {}
    used: dict[str, int] = {}
    for slot, filename, content, mime in uploads:
        n = used.get(slot, 0)
        used[slot] = n + 1
        key = slot if n == 0 else f"{slot}_{n}"
        binary[key] = (filename, content, mime)
        artifacts[key] = {
            "filename": filename,
            "mime_type": mime,
            "bytes": len(content),
            "artifact_id": key,
        }
    save_task_binaries(case_id, binary or None)
    control_plane.create_case(case_id, goal, artifacts, task_name=name)
    if (schedule_root or "").strip():
        row = control_plane.get_case(case_id)
        if row:
            state = dict(row["state"] or {})
            state["schedule_root"] = schedule_root.strip()
            control_plane.update_case(case_id, state=state, status="new")
    control_plane.append_event(
        case_id,
        kind="case.created",
        actor="user",
        status="new",
        status_message=f"Принял задачу: {goal[:180]}",
        payload={"requested_by": requested_by, "files": [fname for _s, fname, _c, _m in uploads], "task_name": name},
    )
    control_plane.update_case(case_id, status="running")
    background_tasks.add_task(_invoke_step, case_id)
    return {"ok": True, "case_id": case_id, "task_id": case_id, "status": "running", "task_name": name}


@router.patch("/cases/{case_id}")
def patch_case_name(case_id: str, body: CaseNameIn) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    name = _normalize_task_name(body.task_name)
    state = dict(row["state"] or {})
    state["task_name"] = name
    control_plane.update_case(case_id, state=state)
    feed = case_feed(case_id)
    return {
        "ok": True,
        "case_id": case_id,
        "task_id": case_id,
        "task_name": feed["task_name"],
        "title": feed["title"],
        "status": feed["status"],
    }


@router.get("/cases/{case_id}")
def get_case_feed(case_id: str) -> dict[str, Any]:
    return case_feed(case_id)


@router.get("/cases/{case_id}/state")
def get_state(case_id: str) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    return {"case_id": case_id, "status": row["status"], "state": row["state"], "updated_at": _iso(row.get("updated_at"))}


@router.get("/cases/{case_id}/events")
def get_events(case_id: str, after_seq: int = Query(default=0, ge=0)) -> dict[str, Any]:
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    events = collapse_duplicate_events(control_plane.list_events(case_id, after_seq=after_seq))
    return {"case_id": case_id, "events": events, "activity": [event_to_turn(event) for event in events]}


@router.get("/cases/{case_id}/errors")
def get_errors(case_id: str) -> dict[str, Any]:
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    return {"case_id": case_id, "errors": control_plane.list_errors(case_id)}


@router.post("/cases/{case_id}/events")
def post_event(case_id: str, body: CaseEventIn) -> dict[str, Any]:
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    if body.kind in AGENT_EVENT_KINDS and body.actor not in {"agent", body.agent_id or "agent"}:
        # Agents may set actor=agent_id; accept both.
        pass
    event = control_plane.append_event(
        case_id,
        kind=body.kind,
        actor=body.actor,
        agent_id=body.agent_id,
        status=body.status,
        status_message=body.status_message,
        handoff_message=body.handoff_message,
        task_id=body.task_id,
        payload=body.payload,
    )
    return {"ok": True, "event": event, "idempotent": bool(event.get("idempotent"))}


async def _collect_form_uploads(form: Any) -> list[tuple[str, str, bytes, str]]:
    uploads: list[tuple[str, str, bytes, str]] = []

    async def take(upload: Any, slot_hint: str | None = None) -> None:
        if upload is None or isinstance(upload, (str, bytes)):
            return
        if not str(getattr(upload, "filename", "") or "").strip():
            return
        filename, content, mime = await _read_upload(upload, field=slot_hint or "file")
        slot = slot_hint or _artifact_slot(filename)
        uploads.append((slot, filename, content, mime))

    await take(form.get("file"), "excel")
    await take(form.get("surface_file"), "surface")
    getter = form.getlist if hasattr(form, "getlist") else lambda _key: []
    for item in getter("trajectory_files"):
        await take(item, "trajectory")
    for item in getter("schedule_files"):
        await take(item, "schedule_source")
    for item in getter("attachments"):
        await take(item, None)
    for item in getter("files"):
        await take(item, None)
    return uploads


def _merge_case_uploads(case_id: str, uploads: list[tuple[str, str, bytes, str]]) -> list[str]:
    if not uploads:
        return []
    binary = load_task_binaries(case_id)
    row = control_plane.get_case(case_id)
    state = dict((row or {}).get("state") or {})
    artifacts = dict(state.get("artifacts") or {})
    names: list[str] = []
    for slot, filename, content, mime in uploads:
        if slot in {"excel", "surface"}:
            key = slot
        else:
            key = slot
            n = 0
            while key in binary:
                n += 1
                key = f"{slot}_{n}"
        binary[key] = (filename, content, mime)
        artifacts[key] = {
            "filename": filename,
            "mime_type": mime,
            "bytes": len(content),
            "artifact_id": key,
        }
        names.append(filename)
    save_task_binaries(case_id, binary)
    if row:
        state["artifacts"] = artifacts
        control_plane.update_case(case_id, state=state)
    return names


@router.post("/cases/{case_id}/answer")
async def post_answer(case_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    ctype = (request.headers.get("content-type") or "").lower()
    file_names: list[str] = []
    if "multipart/form-data" in ctype:
        form = await request.form()
        question_id = str(form.get("question_id") or form.get("gate_id") or "Q-1").strip() or "Q-1"
        answer = str(form.get("answer") or form.get("human_response") or "").strip()
        requested_by = str(form.get("requested_by") or "mas activity user")
        file_names = _merge_case_uploads(case_id, await _collect_form_uploads(form))
        if not answer and file_names:
            answer = "(файл)"
        if not answer:
            raise HTTPException(status_code=400, detail="answer or file is required")
    else:
        raw = await request.json()
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="JSON object required")
        body = CaseAnswerIn.model_validate(raw)
        question_id = body.question_id
        answer = body.answer
        requested_by = body.requested_by
    row = control_plane.get_case(case_id) or row
    state = dict(row["state"] or {})
    hitl = dict(state.get("hitl") or {})
    answers = dict(hitl.get("answers") or {})
    answers[question_id] = answer
    hitl["answers"] = answers
    hitl["pending"] = False
    state["hitl"] = hitl
    control_plane.update_case(case_id, state=state, status="running")
    control_plane.append_event(
        case_id,
        kind="hitl.answered",
        actor="user",
        status="answered",
        status_message=f"Пользователь ответил: {answer}",
        payload={
            "question_id": question_id,
            "answer": answer,
            "requested_by": requested_by,
            "files": file_names,
        },
    )
    if get_settings().n8n_transport == "unconfigured":
        raise HTTPException(status_code=503, detail=UNCONFIGURED_N8N)
    background_tasks.add_task(_invoke_step, case_id)
    return {"ok": True, "case_id": case_id, "status": "running", "files": file_names}


async def _run_action(request: Request) -> str:
    try:
        raw = await request.json()
    except Exception:
        return "step"
    if not isinstance(raw, dict):
        return "step"
    return str(raw.get("action") or "step").strip().lower() or "step"


def _prepare_case_restart(case_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Keep goal/name/artifacts/data; clear error and step budget for a new run."""
    state = dict(row.get("state") or {})
    state["last_error"] = None
    state["current_task"] = None
    state["status"] = "running"
    state["step_count"] = 0
    hitl = dict(state.get("hitl") or {})
    hitl["pending"] = False
    state["hitl"] = hitl
    control_plane.update_case(case_id, state=state, status="running")
    control_plane.append_event(
        case_id,
        kind="orchestrator.status",
        actor="orchestrator",
        status="running",
        status_message="Перезапуск задачи",
    )
    return state


@router.post("/cases/{case_id}/run")
async def post_run(case_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    action = await _run_action(request)
    if get_settings().n8n_transport == "unconfigured":
        raise HTTPException(status_code=503, detail=UNCONFIGURED_N8N)
    explicit_restart = action in RESTART_ACTIONS and case_is_restartable(row)
    if explicit_restart:
        _prepare_case_restart(case_id, row)
        row = control_plane.get_case(case_id) or row
    elif case_is_restartable(row):
        return {
            "ok": True,
            "accepted": False,
            "skipped": True,
            "case_id": case_id,
            "task_id": case_id,
            "status": row["status"],
            "restartable": True,
        }
    state = row["state"] if isinstance(row["state"], dict) else {}
    if int(state.get("step_count") or 0) >= MAX_STEPS:
        control_plane.update_case(case_id, status="failed")
        control_plane.append_event(
            case_id,
            kind="case.failed",
            actor="orchestrator",
            status="failed",
            status_message=f"Превышен лимит шагов оркестратора ({MAX_STEPS})",
        )
        return {
            "ok": False,
            "accepted": False,
            "case_id": case_id,
            "task_id": case_id,
            "status": "failed",
            "reason": "max_steps",
            "restartable": True,
        }
    if row["status"] != "waiting_user":
        control_plane.update_case(case_id, status="running")
    background_tasks.add_task(_invoke_step, case_id)
    return {
        "ok": True,
        "accepted": True,
        "skipped": False,
        "status": "running",
        "restartable": False,
        "case_id": case_id,
        "task_id": case_id,
    }


@router.get("/cases/{case_id}/stream")
async def stream_case(case_id: str, request: Request) -> StreamingResponse:
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")

    async def gen():
        last = 0
        snapshot = case_feed(case_id)
        yield f"data: {json.dumps({'type': 'snapshot', **snapshot}, default=str)}\n\n"
        events = snapshot.get("events") or []
        if events:
            last = int(events[-1]["event_id"])
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(1.0)
            fresh = control_plane.list_events(case_id, after_seq=last)
            visible_ids = {
                int(event["event_id"])
                for event in collapse_duplicate_events(control_plane.list_events(case_id))
                if event.get("event_id") is not None
            }
            for event in fresh:
                last = int(event["event_id"])
                if int(event["event_id"]) not in visible_ids:
                    continue
                yield f"data: {json.dumps({'type': 'turn', 'turn': event_to_turn(event), 'event': event}, default=str)}\n\n"
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/cases/{case_id}/schedule")
def get_schedule(case_id: str):
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    state = row["state"] if isinstance(row["state"], dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    text = _schedule_out_text(artifacts)
    if not text:
        raise HTTPException(status_code=404, detail="schedule not ready")
    from fastapi.responses import Response

    filename = _schedule_filename(artifacts).replace('"', "")
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/cases/{case_id}/artifacts/{artifact_id}")
def get_artifact(case_id: str, artifact_id: str):
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    files = load_task_binaries(case_id)
    if artifact_id not in files:
        raise HTTPException(status_code=404, detail="artifact not found")
    filename, content, mime = files[artifact_id]
    from fastapi.responses import Response

    return Response(
        content=content,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
