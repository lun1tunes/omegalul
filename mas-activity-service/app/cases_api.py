"""Case control-plane HTTP API (state, events, SSE, HITL, run)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app import artifact_store
from app import case_watch
from app import control_plane
from app.contracts import AGENT_EVENT_KINDS, CaseAnswerIn, CaseEventIn, CaseNameIn, MAX_STEPS
from app.state_shape import (
    artifact_filenames,
    artifacts_from_indexed,
    bump_version,
    decode_hitl_answer,
    flatten_artifacts,
    hitl_answer_text,
    nest_artifacts,
    role_for_artifact_id,
)
from app.orchestrator import OrchestratorError, invoke_orchestrator
from app.schema_view import FINISHED_RESULT_TEXT, build_schema_model
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


def _parse_expected_version(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="expected_version must be an integer") from exc


def _assert_state_version(state: dict[str, Any], expected: int | None) -> None:
    if expected is None:
        return
    current = int(state.get("version") or 0)
    if current != expected:
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected {expected}, got {current}. Reload status.",
        )


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
    value = " ".join(str(event.get("status_message") or "").split())
    kind = str(event.get("kind") or "").strip()
    if value.casefold() in {"none", "null", "undefined", kind.casefold()}:
        return ""
    return value


def _event_display_message(kind: str, event: dict[str, Any]) -> str:
    message = _event_message(event)
    if message:
        return message
    return {
        "agent.accepted": "Агент принял задачу и начал работу.",
        "agent.progress": "Агент продолжает обработку.",
        "agent.result": "Агент завершил работу.",
        "agent.failed": "Агент завершил работу с ошибкой.",
        "hitl.request": "Нужно уточнение от пользователя.",
        "case.failed": "Задача завершилась с ошибкой.",
    }.get(kind, kind or "Событие зафиксировано.")


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
        if kind == "agent.handoff" and out:
            prev = out[-1]
            if str(prev.get("kind") or "") in ORCH_ECHO_KINDS and (
                not _event_message(prev) or _event_message(prev) == msg
            ):
                out.pop()
        if kind in TERMINAL_KINDS:
            while out and str(out[-1].get("kind") or "") in ORCH_ECHO_KINDS and (
                not msg or _event_message(out[-1]) == msg
            ):
                out.pop()
            if out and str(out[-1].get("kind") or "") in TERMINAL_KINDS:
                if msg and not _event_message(out[-1]):
                    out[-1] = event
                continue
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
    if kind == "case.finished":
        # Orchestrator's finish carries an honest summary (what agents actually did); the template
        # is only a fallback for legacy events without one.
        said = str(event.get("status_message") or "").strip()
        summary = said or FINISHED_RESULT_TEXT
        text = f"{said} Результат можно скачать." if said else FINISHED_RESULT_TEXT
        brief = summary
    else:
        display_message = _event_display_message(kind, event)
        summary = display_message
        text = display_message
        brief = display_message
    event_id = event.get("event_id")
    return {
        "at": _iso(event.get("created_at")),
        "stage": kind,
        "status": kind,
        "summary": summary,
        "text": text,
        "brief": brief,
        "from": {"role": left},
        "to": {"role": right or left},
        "from_role": left,
        "to_role": right or left,
        "lane_dir": lane_dir,
        "event_type": kind,
        "event_id": event_id,
        "turn_id": event_id,
        "handoff_message": handoff,
        "kind": turn_kind,
        "details": {
            "kind": kind,
            "agent_id": event.get("agent_id"),
            "handoff_message": handoff,
            "payload": payload,
            "event_id": event_id,
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


def _events_for_rail(row: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = row.get("events")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return None


def _rail_turn_count(row: dict[str, Any]) -> int | None:
    """Collapsed chat length. Omit the rail number when events were not loaded.

    Raw ``event_count`` includes orchestrator echoes; do not show it as turns.
    """
    events = _events_for_rail(row)
    if events is None:
        return None
    return len(collapse_duplicate_events(events))


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
        "turn_count": _rail_turn_count(row),
        "awaiting_human": status == "waiting_user",
        "restartable": case_is_restartable({"status": status}),
    }


def _schedule_filename(artifacts: dict[str, Any]) -> str:
    src = flatten_artifacts(artifacts).get("schedule_source")
    if isinstance(src, dict):
        name = str(src.get("filename") or "").strip()
        if name:
            return name
    if isinstance(src, str) and src.strip():
        return src.strip()
    return "schedule.inc"


def _result_filename(artifacts: dict[str, Any]) -> str:
    """Never advertise the baseline upload as the generated result."""
    out = flatten_artifacts(artifacts).get("schedule_out")
    if isinstance(out, dict):
        name = str(out.get("filename") or "").strip()
        if name:
            return name
    src = Path(_schedule_filename(artifacts))
    stem = src.stem.strip() or "schedule"
    suffix = src.suffix if src.suffix.lower() in {".inc", ".data", ".sch", ".grdecl"} else ".inc"
    if stem.lower().endswith("_result"):
        return f"{stem}{suffix}"
    return f"{stem}_result{suffix}"


def _schedule_out_text(artifacts: dict[str, Any]) -> str | None:
    text = flatten_artifacts(artifacts).get("schedule_out")
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
        "filename": _result_filename(artifacts),
        "byte_length": len(text.encode("utf-8")),
        "inline": True,
        "download_path": f"/cases/{case_id}/schedule",
        "kind": "result",
        "label": "Скачать результат",
    }


def _feed_from_row(
    case_id: str,
    row: dict[str, Any],
    events: list[dict[str, Any]],
    after_seq: int = 0,
) -> dict[str, Any]:
    state = row["state"] if isinstance(row.get("state"), dict) else {}
    turns = [event_to_turn(event) for event in events]
    pending = state.get("hitl") if isinstance(state.get("hitl"), dict) else {}
    questions = pending.get("questions") if isinstance(pending.get("questions"), list) else []
    gate = None
    if row.get("status") == "waiting_user" and questions:
        q0 = questions[0] if isinstance(questions[0], dict) else {}
        gate = {
            "gate_id": q0.get("question_id") or "hitl",
            # Orchestrator marks result reviews (kind=result_approval); everything else is a data/decision request.
            "kind": str(q0.get("kind") or "needs_input"),
            "reason": q0.get("question") or state.get("goal") or "Нужен ответ",
            "questions": questions,
        }
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    attached = artifact_filenames(artifacts)
    name = _normalize_task_name(state.get("task_name"))
    return {
        "ok": True,
        "task_id": case_id,
        "case_id": case_id,
        "task_name": name,
        "title": (name or state.get("goal") or case_id)[:120],
        "objective": state.get("goal") or "",
        "status": row.get("status"),
        "state": state,
        "attached_files": attached,
        "awaiting_human": row.get("status") == "waiting_user",
        "human_gate": gate,
        "activity": turns,
        "events": events,
        "schema": build_schema_model(events, state=state, status=row.get("status")),
        "after_seq": after_seq,
        "schedule_artifact": _schedule_artifact(case_id, artifacts),
        "restartable": case_is_restartable(row),
    }


def case_feed(case_id: str, after_seq: int = 0) -> dict[str, Any]:
    snap = control_plane.snapshot(case_id, after_seq=after_seq)
    row = snap["case"]
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    events = collapse_duplicate_events(snap["events"])
    return _feed_from_row(case_id, row, events, after_seq)


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


def _filename_base(name: str) -> str:
    return Path(str(name or "").strip()).name.lower()


def _promote_schedule_root(
    uploads: list[tuple[str, str, bytes, str]],
    schedule_root: str = "",
) -> list[tuple[str, str, bytes, str]]:
    """Ensure the named root .INC is the `schedule_source` slot, not whichever file arrived first."""
    root = _filename_base(schedule_root)
    if not root:
        return list(uploads)
    match_i = next(
        (
            i
            for i, (slot, filename, _content, _mime) in enumerate(uploads)
            if slot == "schedule_source" and _filename_base(filename) == root
        ),
        None,
    )
    if match_i is None:
        return list(uploads)
    ordered = list(uploads)
    item = ordered.pop(match_i)
    insert_at = next((i for i, (slot, *_rest) in enumerate(ordered) if slot == "schedule_source"), len(ordered))
    ordered.insert(insert_at, item)
    return ordered


def _index_uploads(uploads: list[tuple[str, str, bytes, str]]) -> dict[str, tuple[str, bytes, str]]:
    binary: dict[str, tuple[str, bytes, str]] = {}
    used: dict[str, int] = {}
    for slot, filename, content, mime in uploads:
        n = used.get(slot, 0)
        used[slot] = n + 1
        key = slot if n == 0 else f"{slot}_{n}"
        binary[key] = (filename, content, mime)
    return binary


def _new_case_id() -> str:
    return f"CASE-{int(time.time()):x}-{secrets.token_hex(3)}"


async def _invoke_step(case_id: str) -> None:
    await _invoke_action(case_id, action="step")


async def _invoke_create(case_id: str) -> None:
    await _invoke_action(case_id, action="create")


async def _invoke_action(case_id: str, *, action: str) -> None:
    try:
        row = control_plane.get_case(case_id)
        state = dict(row["state"] or {}) if row else {}
        await invoke_orchestrator(
            {
                "case_id": case_id,
                "action": action,
                "task_description": str(state.get("goal") or ""),
                "task_name": str(state.get("task_name") or ""),
                "requested_by": "mas activity user",
                "artifacts": state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {},
            },
            timeout_s=180.0,
        )
    except OrchestratorError as exc:
        logger.error("orchestrator %s failed case_id=%s: %s", action, case_id, exc)
        try:
            row = control_plane.get_case(case_id)
            state = dict(row["state"] or {}) if row else {}
            state["last_error"] = {"message": str(exc)[:500]}
            control_plane.update_case_and_append(
                case_id,
                state=state,
                status="failed",
                kind="case.failed",
                actor="orchestrator",
                event_status="failed",
                status_message=str(exc)[:500],
                payload={"status_code": exc.status_code},
            )
        except KeyError:
            control_plane.append_event(
                case_id,
                kind="case.failed",
                actor="orchestrator",
                status="failed",
                status_message=str(exc)[:500],
                payload={"status_code": exc.status_code},
            )


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
    settings = get_settings()
    if settings.n8n_transport == "unconfigured":
        raise HTTPException(status_code=503, detail=UNCONFIGURED_N8N)
    if settings.control_plane_required and not control_plane.configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Control-plane proxy is required. Set "
                "CONTROL_PLANE_PROXY_URL to the active n8n "
                "/webhook/mas-control-plane endpoint."
            ),
        )
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

    binary = _index_uploads(_promote_schedule_root(uploads, schedule_root))
    artifacts: dict[str, Any] = artifacts_from_indexed(binary)
    if artifact_store.configured():
        for key, (filename, content, mime) in binary.items():
            try:
                await artifact_store.put(case_id, key, filename, content, mime)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"artifact proxy upload failed: {exc}") from exc
    else:
        save_task_binaries(case_id, binary or None)
    extra = {}
    if (schedule_root or "").strip():
        extra["schedule_root"] = schedule_root.strip()
    control_plane.create_case(
        case_id,
        goal,
        artifacts,
        task_name=name,
        extra_state=extra or None,
        status="running",
        initial_event={
            "kind": "case.created",
            "actor": "user",
            "status": "new",
            "status_message": f"Принял задачу: {goal[:180]}",
            "payload": {
                "requested_by": requested_by,
                "files": [fname for _s, fname, _c, _m in uploads],
                "task_name": name,
            },
        },
    )
    background_tasks.add_task(_invoke_create, case_id)
    return {"ok": True, "case_id": case_id, "task_id": case_id, "status": "running", "task_name": name}


@router.patch("/cases/{case_id}")
def patch_case_name(case_id: str, body: CaseNameIn) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    name = _normalize_task_name(body.task_name)
    state = dict(row["state"] or {})
    state["task_name"] = name
    control_plane.update_case(case_id, state=state, status=row["status"])
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
    snap = control_plane.snapshot(case_id, after_seq=after_seq)
    if snap["case"] is None:
        raise HTTPException(status_code=404, detail="case not found")
    events = collapse_duplicate_events(snap["events"])
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


async def _merge_case_uploads(case_id: str, uploads: list[tuple[str, str, bytes, str]]) -> list[str]:
    if not uploads:
        return []
    binary = load_task_binaries(case_id) if not artifact_store.configured() else {}
    row = control_plane.get_case(case_id)
    state = dict((row or {}).get("state") or {})
    artifacts = nest_artifacts(state.get("artifacts") or {})
    used = {key for key in flatten_artifacts(artifacts).keys() if key != "diff"} | set(binary.keys())
    used.discard("diff")
    names: list[str] = []
    for slot, filename, content, mime in uploads:
        if slot in {"excel", "surface"}:
            key = slot
        else:
            key = slot
            n = 0
            while key in used:
                n += 1
                key = f"{slot}_{n}"
        used.add(key)
        binary[key] = (filename, content, mime)
        flat = flatten_artifacts(artifacts)
        flat[key] = {
            "filename": filename,
            "mime_type": mime,
            "bytes": len(content),
            "artifact_id": key,
            "role": role_for_artifact_id(key),
        }
        artifacts = nest_artifacts(flat)
        names.append(filename)
    if artifact_store.configured():
        for key, (filename, content, mime) in binary.items():
            try:
                await artifact_store.put(case_id, key, filename, content, mime)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"artifact proxy upload failed: {exc}") from exc
    else:
        save_task_binaries(case_id, binary)
    if row:
        state["artifacts"] = artifacts
        control_plane.update_case(case_id, state=state, status=row.get("status") or "running")
    return names


def _option_label(hitl: dict[str, Any], question_id: str, choice: str) -> str:
    """Human label for a clicked option.

    Match the question by id first; questions without an id are only considered when no
    question carries the requested id (legacy single-question gates), so an id-less question
    can never shadow the one the engineer actually answered.
    """
    questions = [q for q in (hitl.get("questions") if isinstance(hitl.get("questions"), list) else []) if isinstance(q, dict)]
    wanted = str(question_id or "")

    def label_in(question: dict[str, Any]) -> str | None:
        for option in question.get("options") or []:
            if isinstance(option, dict) and str(option.get("value") or "") == choice:
                return str(option.get("label") or choice)
            if isinstance(option, str) and option == choice:
                return option
        return None

    exact = [q for q in questions if str(q.get("question_id") or q.get("id") or "") == wanted and wanted]
    for question in exact:
        found = label_in(question)
        if found is not None:
            return found
    if not exact:
        for question in questions:
            if str(question.get("question_id") or question.get("id") or ""):
                continue
            found = label_in(question)
            if found is not None:
                return found
    return choice


@router.post("/cases/{case_id}/answer")
async def post_answer(case_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    ctype = (request.headers.get("content-type") or "").lower()
    file_names: list[str] = []
    expected_version: int | None = None
    if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        form = await request.form()
        question_id = str(form.get("question_id") or form.get("gate_id") or "Q-1").strip() or "Q-1"
        answer = str(form.get("answer") or form.get("human_response") or "").strip()
        requested_by = str(form.get("requested_by") or "mas activity user")
        expected_version = _parse_expected_version(form.get("expected_version"))
        choice = str(form.get("choice") or "").strip() or None
        file_names = await _merge_case_uploads(case_id, await _collect_form_uploads(form))
        if not answer and file_names:
            answer = "(файл)"
        if not answer and not choice:
            raise HTTPException(status_code=400, detail="answer or file is required")
    else:
        raw = await request.json()
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="JSON object required")
        body = CaseAnswerIn.model_validate(raw)
        question_id = body.question_id
        answer = body.answer
        requested_by = body.requested_by
        expected_version = body.expected_version
        choice = (body.choice or "").strip() or None
    row = control_plane.get_case(case_id) or row
    state = dict(row["state"] or {})
    _assert_state_version(state, expected_version)
    hitl = dict(state.get("hitl") or {})
    answers = dict(hitl.get("answers") or {})
    parsed_answer = decode_hitl_answer(answer)
    if choice:
        # Option button: keep the machine value and the human label/free text side by side.
        label = _option_label(hitl, question_id, choice)
        parsed_answer = {
            "choice": choice,
            "text": parsed_answer if isinstance(parsed_answer, str) and parsed_answer else label,
            "label": label,
        }
    answers[question_id] = parsed_answer
    hitl["answers"] = answers
    hitl["pending"] = False
    state["hitl"] = hitl
    bump_version(state)
    answer_text = hitl_answer_text(parsed_answer)
    control_plane.update_case_and_append(
        case_id,
        state=state,
        status="running",
        kind="hitl.answered",
        actor="user",
        event_status="answered",
        status_message=f"Пользователь ответил: {answer_text}",
        payload={
            "question_id": question_id,
            "answer": parsed_answer,
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
    bump_version(state)
    control_plane.update_case_and_append(
        case_id,
        state=state,
        status="running",
        kind="orchestrator.status",
        actor="orchestrator",
        event_status="running",
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
        state = _prepare_case_restart(case_id, row)
        row = {**row, "state": state, "status": "running"}
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
        control_plane.update_case_and_append(
            case_id,
            state=state,
            status="failed",
            kind="case.failed",
            actor="orchestrator",
            event_status="failed",
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
        control_plane.update_case(case_id, state=state, status="running")
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


def _stream_meta(feed: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": feed.get("status"),
        "awaiting_human": feed.get("awaiting_human"),
        "human_gate": feed.get("human_gate"),
        "restartable": feed.get("restartable"),
        "schedule_artifact": feed.get("schedule_artifact"),
        "state": feed.get("state"),
        "task_name": feed.get("task_name"),
        "title": feed.get("title"),
        "objective": feed.get("objective"),
        "attached_files": feed.get("attached_files"),
    }


@router.get("/cases/{case_id}/stream")
async def stream_case(case_id: str, request: Request) -> StreamingResponse:
    snap = control_plane.snapshot(case_id)
    if snap["case"] is None:
        raise HTTPException(status_code=404, detail="case not found")

    async def gen():
        all_raw: list[dict[str, Any]] = list(snap["events"] or [])
        snapshot = _feed_from_row(case_id, snap["case"], collapse_duplicate_events(all_raw), 0)
        yield f"data: {json.dumps({'type': 'snapshot', **snapshot}, default=str)}\n\n"
        events = snapshot.get("events") or []
        seen_ids = {
            int(event["event_id"])
            for event in events
            if event.get("event_id") is not None
        }
        last = max(
            (
                int(event["event_id"])
                for event in all_raw
                if event.get("event_id") is not None
            ),
            default=0,
        )
        last_updated = str((snap["case"] or {}).get("updated_at") or "")
        queue = await case_watch.subscribe(case_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    fresh = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                    continue
                row = (fresh or {}).get("case")
                if row is None:
                    break
                raw_new = [
                    event
                    for event in list((fresh or {}).get("events") or [])
                    if event.get("event_id") is None or int(event["event_id"]) > last
                ]
                if raw_new:
                    all_raw.extend(raw_new)
                    last = max(
                        last,
                        max(
                            int(event["event_id"])
                            for event in raw_new
                            if event.get("event_id") is not None
                        ),
                    )
                collapsed = collapse_duplicate_events(all_raw)
                emit = [
                    event
                    for event in collapsed
                    if event.get("event_id") is not None and int(event["event_id"]) not in seen_ids
                ]
                feed = _feed_from_row(case_id, row, [], last)
                meta = _stream_meta(feed)
                updated = str(row.get("updated_at") or "")
                for event in emit:
                    seen_ids.add(int(event["event_id"]))
                    yield f"data: {json.dumps({'type': 'turn', 'turn': event_to_turn(event), 'event': event, **meta}, default=str)}\n\n"
                if not emit and updated != last_updated:
                    yield f"data: {json.dumps({'type': 'meta', **meta}, default=str)}\n\n"
                last_updated = updated
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            await case_watch.unsubscribe(case_id, queue)

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

    filename = _result_filename(artifacts).replace('"', "")
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/cases/{case_id}/artifacts/{artifact_id}")
async def get_artifact(case_id: str, artifact_id: str):
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    if artifact_store.configured():
        try:
            filename, content, mime = await artifact_store.get(case_id, artifact_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"artifact proxy download failed: {exc}") from exc
    else:
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
