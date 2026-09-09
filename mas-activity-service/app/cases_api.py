"""Case control-plane HTTP API (state, events, SSE, HITL, run)."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from app import artifact_store
from app import case_log
from app import case_watch
from app import control_plane
from app.contracts import AGENT_EVENT_KINDS, AgentRegistryIn, CaseAnswerIn, CaseEventIn, CaseNameIn, MAX_STEPS, is_trace_kind
from app.state_shape import (
    artifact_cards,
    artifact_filenames,
    artifact_kind,
    artifact_text,
    artifacts_from_indexed,
    bump_version,
    flatten_artifacts,
    nest_artifacts,
    role_for_artifact_id,
    sanitize_plan,
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
RESTARTABLE_STATUSES = frozenset({"done", "failed", "retryable_error", "cancelled"})
RESTART_ACTIONS = frozenset({"retry", "restart"})
CANCEL_FROM_STATUSES = frozenset({"running", "waiting_user", "waiting_agent", "failed"})
STALE_RESUME_STATUSES = frozenset({"running", "waiting_agent"})
STATUS_MESSAGE_KINDS = ("case.failed", "case.cancelled", "case.finished", "agent.failed")


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


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _seconds_since(value: Any) -> float | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _last_activity_at(row: dict[str, Any], events: list[dict[str, Any]]) -> Any:
    for event in reversed(events):
        if event.get("created_at"):
            return event.get("created_at")
    return row.get("updated_at")


def _is_resume_stale(row: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    status = str(row.get("status") or "")
    if status not in STALE_RESUME_STATUSES:
        return False
    age = _seconds_since(_last_activity_at(row, events))
    if age is None:
        return False
    return age >= float(get_settings().resume_stale_s)


def _case_status_message(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        kind = str(event.get("kind") or "")
        if kind in STATUS_MESSAGE_KINDS:
            message = _event_message(event)
            if message:
                return message
    return ""


def _keep_input_artifacts(artifacts: Any) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, item in flatten_artifacts(artifacts).items():
        if artifact_kind(item) == "input":
            kept[key] = item
    return nest_artifacts(kept)


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
    if kind.startswith("orchestrator.") or kind in {"case.finished", "case.failed", "case.cancelled", "system.node_error"}:
        return ORCH, None, "none"
    if agent and agent not in {actor, ORCH}:
        return actor, agent, "out"
    if actor != ORCH:
        return ORCH, actor, "none"
    return ORCH, None, "none"


ORCH_ECHO_KINDS = {"orchestrator.status", "orchestrator.decision"}
AGENT_DUP_KINDS = {"agent.result", "agent.failed", "agent.accepted", "agent.progress"}
TERMINAL_KINDS = {"case.finished", "case.failed", "case.cancelled"}


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
        "case.cancelled": "Задача закрыта инженером.",
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
        if is_trace_kind(kind):
            # Developer trace rows (tool calls, technical notes) live in the «Лог» tab only.
            continue
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
        text = summary
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
    text = artifact_text(artifacts, "schedule_out")
    if isinstance(text, str) and len(text.strip()) > 20:
        return text
    return None


def _producers_from_events(events: list[dict[str, Any]] | None) -> dict[str, str]:
    """artifact_id → agent_id for states merged before ``producer`` existed (from agent.result events)."""
    out: dict[str, str] = {}
    for event in events or []:
        if not isinstance(event, dict) or str(event.get("kind") or "") != "agent.result":
            continue
        agent = str(event.get("agent_id") or event.get("actor") or "").strip()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        ids = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
        for aid in ids:
            if agent and isinstance(aid, str) and aid:
                out[aid] = agent
    return out


def _artifact_cards(case_id: str, state: dict[str, Any], events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Artifact cards of a case with download paths; the engineer's inputs first, then agent results."""
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    cards = artifact_cards(artifacts, producers=_producers_from_events(events))
    for card in cards:
        if card["role"] == "schedule_out" and not (flatten_artifacts(artifacts).get("schedule_out") or {}).get("filename"):
            card["filename"] = _result_filename(artifacts)
        elif card["role"] == "diff" and not (flatten_artifacts(artifacts).get("diff") or {}).get("filename"):
            card["filename"] = f"{Path(_result_filename(artifacts)).stem}.diff"
        card["download_path"] = f"/cases/{case_id}/artifacts/{card['artifact_id']}"
    return cards


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
    cards = _artifact_cards(case_id, state, events)
    return {
        "ok": True,
        "task_id": case_id,
        "case_id": case_id,
        "task_name": name,
        "title": (name or state.get("goal") or case_id)[:120],
        "objective": state.get("goal") or "",
        "status": row.get("status"),
        "status_message": _case_status_message(events),
        "state": state,
        "attached_files": attached,
        "awaiting_human": row.get("status") == "waiting_user",
        "human_gate": gate,
        "activity": turns,
        "events": events,
        "schema": build_schema_model(events, state=state, status=row.get("status")),
        "after_seq": after_seq,
        # Phase 1.5: the case result is the set of agent deliverables (cards by producer), not one file.
        "artifacts": cards,
        "deliverables": [card for card in cards if card["kind"] == "deliverable"],
        # O13: the orchestrator's decomposition (state.plan) — the UI shows it under the task statement.
        "plan": sanitize_plan(state.get("plan")),
        "restartable": case_is_restartable(row),
        "resume_stale": _is_resume_stale(row, events),
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


async def _invoke_resume(case_id: str, extra: dict[str, Any] | None = None) -> None:
    await _invoke_action(case_id, action="resume", extra=extra or {})


async def _invoke_action(case_id: str, *, action: str, extra: dict[str, Any] | None = None) -> None:
    try:
        row = control_plane.get_case(case_id)
        state = dict(row["state"] or {}) if row else {}
        payload: dict[str, Any] = {
            "case_id": case_id,
            "action": action,
            "task_description": str(state.get("goal") or ""),
            "task_name": str(state.get("task_name") or ""),
            "requested_by": "mas activity user",
            "artifacts": state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {},
        }
        if extra:
            payload.update({key: value for key, value in extra.items() if value is not None})
        await invoke_orchestrator(
            payload,
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
                status_message="Не удалось передать задачу оркестратору. Проверьте адрес оркестратора в настройках и повторите.",
                payload={"status_code": exc.status_code, "detail": str(exc)[:500]},
            )
        except KeyError:
            control_plane.append_event(
                case_id,
                kind="case.failed",
                actor="orchestrator",
                status="failed",
                status_message="Не удалось передать задачу оркестратору. Проверьте адрес оркестратора в настройках и повторите.",
                payload={"status_code": exc.status_code, "detail": str(exc)[:500]},
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
    artifacts: dict[str, Any] = artifacts_from_indexed(binary, created_at=datetime.now(timezone.utc).isoformat())
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


def _n8n_base() -> str:
    """Browser-facing n8n root for execution links in the developer log ('' when n8n is not configured)."""
    try:
        return get_settings().n8n_public_base
    except Exception:  # noqa: BLE001 — the log must render without n8n configured
        return ""


@router.get("/cases/{case_id}/log")
def get_case_log(case_id: str, format: str = Query(default="json")) -> Any:
    """Developer log («Лог» tab): every event with level/source/step, n8n links, error stacks.

    ``?format=ndjson`` downloads the same records one per line — the file to attach to a bug report
    or to feed ``scripts/mas_trace_case.py``.
    """
    log = case_log.build_case_log(case_id, n8n_base=_n8n_base())
    if log is None:
        raise HTTPException(status_code=404, detail="case not found")
    if format == "ndjson":
        return PlainTextResponse(
            case_log.to_ndjson(log),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{case_id}.log.ndjson"'},
        )
    return log


def _record_execution_from_payload(case_id: str, actor: str, payload: dict[str, Any]) -> None:
    """Any producer that knows its n8n execution id maps it to the case (agent workflows send it on
    ``agent.accepted``): the Error Trigger of that workflow can then attribute its failure to the case."""
    execution_id = str(payload.get("execution_id") or "").strip()
    if not execution_id:
        return
    workflow_name = str(payload.get("workflow_name") or actor or "agent").strip()
    try:
        control_plane.record_execution(execution_id, case_id, workflow_name=workflow_name)
    except Exception as exc:  # noqa: BLE001 — a missing link must not fail the event write
        logger.warning("record_execution failed case_id=%s execution_id=%s: %s", case_id, execution_id, exc)


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
    _record_execution_from_payload(case_id, body.actor, body.payload)
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
            "kind": "input",
            "producer": "user",
            "created_at": datetime.now(timezone.utc).isoformat(),
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
    if get_settings().n8n_transport == "unconfigured":
        raise HTTPException(status_code=503, detail=UNCONFIGURED_N8N)
    raw_answer: dict[str, Any] = {"text": answer, "files": file_names}
    if choice:
        label = _option_label(dict(state.get("hitl") or {}), question_id, choice)
        raw_answer["choice"] = choice
        raw_answer["label"] = label
        if not raw_answer.get("text"):
            raw_answer["text"] = label
    extra = {
        "source": "human",
        "gate_id": question_id,
        "expected_version": expected_version,
        "human_response": json.dumps(raw_answer, ensure_ascii=False),
        "requested_by": requested_by,
    }
    background_tasks.add_task(_invoke_resume, case_id, extra)
    return {"ok": True, "case_id": case_id, "status": str(row.get("status") or "waiting_user"), "files": file_names}


async def _run_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _prepare_case_restart(case_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Keep goal/name/input artifacts; clear agents, journal, plan, HITL and the step budget."""
    state = dict(row.get("state") or {})
    state["last_error"] = None
    state["current_task"] = None
    state["status"] = "running"
    state["step_count"] = 0
    state["error_count"] = 0
    state["agents"] = {}
    state["ledger"] = {
        "history": [],
        "stall_count": 0,
        "last_human_step": 0,
        "reviews": 0,
        "verify_rejections": 0,
        "parse_failures": 0,
    }
    state["plan"] = []
    state["data"] = {}
    state["hitl"] = {"pending": False, "questions": [], "answers": {}}
    state["artifacts"] = _keep_input_artifacts(state.get("artifacts"))
    bump_version(state)
    control_plane.update_case_and_append(
        case_id,
        state=state,
        status="running",
        kind="orchestrator.status",
        actor="orchestrator",
        event_status="running",
        status_message="Перезапуск задачи с исходными файлами",
    )
    return state


@router.post("/cases/{case_id}/run")
async def post_run(case_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    body = await _run_body(request)
    action = str(body.get("action") or "step").strip().lower() or "step"
    if get_settings().n8n_transport == "unconfigured":
        raise HTTPException(status_code=503, detail=UNCONFIGURED_N8N)
    if action == "cancel":
        status = str(row.get("status") or "")
        if status not in CANCEL_FROM_STATUSES:
            raise HTTPException(status_code=409, detail="Эту задачу уже нельзя закрыть.")
        state = dict(row.get("state") or {})
        hitl = dict(state.get("hitl") or {})
        hitl["pending"] = False
        state["hitl"] = hitl
        state["current_task"] = None
        state["status"] = "cancelled"
        bump_version(state)
        control_plane.update_case_and_append(
            case_id,
            state=state,
            status="cancelled",
            kind="case.cancelled",
            actor="user",
            event_status="cancelled",
            status_message="Задача закрыта инженером",
        )
        return {
            "ok": True,
            "accepted": True,
            "skipped": False,
            "status": "cancelled",
            "restartable": True,
            "case_id": case_id,
            "task_id": case_id,
        }
    if action == "resume":
        source = str(body.get("source") or "agent").strip().lower()
        if source == "human":
            raise HTTPException(status_code=400, detail="Ответ инженера отправляйте через поле ответа в задаче.")
        if source not in {"agent", "system"}:
            raise HTTPException(status_code=400, detail="Будить задачу может агент или система.")
        if source == "system":
            events = control_plane.list_events(case_id)
            status = str(row.get("status") or "")
            if status not in STALE_RESUME_STATUSES:
                raise HTTPException(status_code=409, detail="Задачу в этом состоянии нельзя продолжить.")
            if not _is_resume_stale(row, events):
                raise HTTPException(status_code=409, detail="Оркестратор ещё работает, подождите…")
        extra = {
            "source": source,
            "task_id": str(body.get("task_id") or ""),
            "agent_id": str(body.get("agent_id") or ""),
            # A long agent's final ``agent_result`` (kit ``ActivityClient.finish_task``) rides in ``human_response``;
            # the orchestrator merges it like a synchronous result. ``payload`` / ``result`` are older spellings.
            "human_response": json.dumps(
                next((body[k] for k in ("agent_result", "payload", "result") if isinstance(body.get(k), dict)), {}),
                ensure_ascii=False,
            ),
            "requested_by": str(body.get("requested_by") or "mas activity user"),
        }
        background_tasks.add_task(_invoke_resume, case_id, extra)
        return {
            "ok": True,
            "accepted": True,
            "skipped": False,
            "status": "running",
            "restartable": False,
            "case_id": case_id,
            "task_id": case_id,
        }
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
        "resume_stale": feed.get("resume_stale"),
        "status_message": feed.get("status_message"),
        "artifacts": feed.get("artifacts"),
        "deliverables": feed.get("deliverables"),
        "plan": feed.get("plan"),
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

    n8n_base = _n8n_base()

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
                # Developer log rows ride the same stream: chat events carry their record inside the
                # turn message, trace.* events (hidden from the chat) go as `trace` messages.
                log_by_id = {
                    record["seq"]: record
                    for record in case_log.records_from_events(all_raw, n8n_base=n8n_base)
                    if record.get("seq") is not None
                }
                for event in raw_new:
                    if event.get("event_id") is None or not is_trace_kind(event.get("kind")):
                        continue
                    record = log_by_id.get(int(event["event_id"]))
                    if record is not None:
                        yield f"data: {json.dumps({'type': 'trace', 'log': record, **meta}, default=str)}\n\n"
                for event in emit:
                    seen_ids.add(int(event["event_id"]))
                    record = log_by_id.get(int(event["event_id"]))
                    yield f"data: {json.dumps({'type': 'turn', 'turn': event_to_turn(event), 'event': event, 'log': record, **meta}, default=str)}\n\n"
                if not emit and updated != last_updated:
                    yield f"data: {json.dumps({'type': 'meta', **meta}, default=str)}\n\n"
                last_updated = updated
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            await case_watch.unsubscribe(case_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


_AGENT_JSON_LISTS = ("input_required", "output_provides")
_AGENT_JSON_OBJECTS = ("invoke", "input_schema", "output_schema")


def _agent_row_view(row: dict[str, Any]) -> dict[str, Any]:
    """One ``agent_registry`` row as the API shows it (JSON columns may arrive as strings from the proxy)."""

    def _json(value: Any, default: Any) -> Any:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return default
        return value if isinstance(value, type(default)) else default

    enabled = row.get("enabled")
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("t", "true", "1", "yes")
    return {
        "agent_id": str(row.get("agent_id")),
        "title": str(row.get("title") or row.get("agent_id")),
        "when_to_use": str(row.get("when_to_use") or ""),
        **{k: _json(row.get(k), []) for k in _AGENT_JSON_LISTS},
        **{k: _json(row.get(k), {}) for k in _AGENT_JSON_OBJECTS},
        "hitl_policy": str(row.get("hitl_policy") or "agent_asks"),
        "enabled": True if enabled is None else bool(enabled),
        "version": str(row.get("version") or "1"),
    }


@router.get("/agents")
def list_agents() -> dict[str, Any]:
    """Agent registry (Phase 2, executable): the UI hardcodes no agent, the orchestrator reads the same rows."""
    rows = [
        _agent_row_view(row)
        for row in control_plane.list_agents()
        if isinstance(row, dict) and row.get("agent_id")
    ]
    return {"agents": rows}


@router.put("/agents/{agent_id}")
def upsert_agent(agent_id: str, body: AgentRegistryIn) -> dict[str, Any]:
    """Bind or edit one agent without touching the orchestrator JSON.

    Field path (n8n UI-only): after importing an agent workflow the engineer copies its new id from the URL
    and sends ``{"invoke": {"kind": "n8n_workflow", "workflow_id": "<id>"}}`` here (FastAPI ``/docs`` on the
    Windows workstation). Missing fields keep the stored values; a new ``agent_id`` needs at least ``title``
    and ``when_to_use`` so the Decision LLM can choose it.
    """
    agent_id = agent_id.strip()
    if not agent_id or not all(ch.isalnum() or ch in "_-" for ch in agent_id):
        raise HTTPException(status_code=400, detail="agent_id must be [A-Za-z0-9_-]+")
    current = next(
        (
            _agent_row_view(row)
            for row in control_plane.list_agents()
            if isinstance(row, dict) and str(row.get("agent_id")) == agent_id
        ),
        None,
    )
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if current is None:
        if not patch.get("title") or not patch.get("when_to_use"):
            raise HTTPException(status_code=400, detail="new agent needs title and when_to_use")
        current = _agent_row_view({"agent_id": agent_id, **patch})
    merged = {**current, **patch, "agent_id": agent_id}
    control_plane.upsert_agent(merged)
    return {"ok": True, "agent": merged}


@router.get("/cases/{case_id}/artifacts")
def list_artifacts(
    case_id: str,
    kind: str = Query(default=""),
    producer: str = Query(default=""),
) -> dict[str, Any]:
    """Artifact cards of the case: ``{artifact_id, role, kind, producer, filename, bytes, download_path}``.

    ``kind`` = input | intermediate | deliverable; ``producer`` = user | <agent_id>.
    """
    snap = control_plane.snapshot(case_id)
    row = snap["case"]
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    state = row["state"] if isinstance(row.get("state"), dict) else {}
    cards = _artifact_cards(case_id, state, snap.get("events") or [])
    want_kind = (kind or "").strip().lower()
    want_producer = (producer or "").strip()
    if want_kind:
        cards = [card for card in cards if card["kind"] == want_kind]
    if want_producer:
        cards = [card for card in cards if card["producer"] == want_producer]
    return {"ok": True, "case_id": case_id, "artifacts": cards}


def content_disposition(filename: str) -> str:
    """``attachment`` header that survives Russian file names (HTTP headers are latin-1; RFC 5987 carries UTF-8)."""
    from urllib.parse import quote

    name = str(filename or "artifact").replace('"', "").replace("\r", "").replace("\n", "")
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip() or "artifact"
    if ascii_name == name:
        return f'attachment; filename="{name}"'
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _slug_artifact_id(raw: str, filename: str) -> str:
    base = (raw or "").strip() or Path(filename or "artifact").stem
    slug = "".join(ch if ch.isalnum() else "_" for ch in base.lower()).strip("_")
    return slug or "artifact"


@router.post("/cases/{case_id}/artifacts")
async def upload_agent_artifact(
    case_id: str,
    file: UploadFile = File(...),
    artifact_id: str = Form(default=""),
    producer: str = Form(default=""),
    summary: str = Form(default=""),
    kind: str = Form(default="deliverable"),
) -> dict[str, Any]:
    """An agent stores a binary deliverable (report, workbook, plot) and gets back its card.

    The agent puts the returned card under ``agent_result.artifacts[artifact_id]``; the orchestrator
    merges it into ``state.artifacts`` and the feed shows it as that agent's result
    (``kind=deliverable``, ``producer=<agent_id>``). Text deliverables of the Schedule Builder
    (``schedule_out``, ``diff``) still travel inline in ``agent_result`` — this route is for bytes.
    Kit call: ``ActivityClient.upload(artifact_id, filename, content, mime)``.
    """
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    want_kind = (kind or "deliverable").strip().lower()
    if want_kind not in {"deliverable", "intermediate"}:
        raise HTTPException(status_code=400, detail="kind must be deliverable or intermediate")
    who = (producer or "").strip()
    if not who or who == "user":
        raise HTTPException(status_code=400, detail="producer must be the agent_id")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    filename = Path(file.filename or "artifact").name
    mime = file.content_type or "application/octet-stream"

    state = dict(row.get("state") or {})
    artifacts = nest_artifacts(state.get("artifacts") or {})
    flat = flatten_artifacts(artifacts)
    binary = load_task_binaries(case_id) if not artifact_store.configured() else {}
    used = set(flat.keys()) | set(binary.keys())
    base = _slug_artifact_id(artifact_id, filename)
    if role_for_artifact_id(base) != "attachment":
        base = f"out_{base}"  # never take an input slot (excel, schedule_source, …): agent bytes are attachments by role
    key, n = base, 0
    while key in used:
        n += 1
        key = f"{base}_{n}"
    card = {
        "artifact_id": key,
        "role": role_for_artifact_id(key),
        "kind": want_kind,
        "producer": who,
        "filename": filename,
        "mime_type": mime,
        "bytes": len(content),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if summary.strip():
        card["summary"] = summary.strip()[:400]
    if artifact_store.configured():
        try:
            await artifact_store.put(case_id, key, filename, content, mime)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"artifact proxy upload failed: {exc}") from exc
    else:
        binary[key] = (filename, content, mime)
        save_task_binaries(case_id, binary)
    flat[key] = card
    state["artifacts"] = nest_artifacts(flat)
    control_plane.update_case(case_id, state=state, status=row.get("status") or "running")
    return {"ok": True, "case_id": case_id, "artifact": {**card, "download_path": f"/cases/{case_id}/artifacts/{key}"}}


@router.get("/cases/{case_id}/schedule")
def get_schedule(case_id: str):
    """Compatibility alias for the Schedule Builder deliverable (``role == schedule_out``).

    Kept for older harness scripts; new clients read ``GET /cases/{id}/artifacts`` and download by
    ``download_path`` (``/cases/{id}/artifacts/{artifact_id}``).
    """
    row = control_plane.get_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="case not found")
    state = row["state"] if isinstance(row["state"], dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    text = _schedule_out_text(artifacts)
    if not text:
        raise HTTPException(status_code=404, detail="schedule not ready")
    from fastapi.responses import Response

    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition(_result_filename(artifacts))},
    )


def _inline_artifact(case_id: str, artifact_id: str) -> tuple[str, bytes, str] | None:
    """Text deliverables (schedule_out / diff) live inline in state, not in the blob store."""
    # One webhook (case + events), same as the feed: cards are built the same way everywhere,
    # including producers of legacy artifacts inferred from agent.result events.
    snap = control_plane.snapshot(case_id)
    row = snap["case"]
    state = row["state"] if row and isinstance(row.get("state"), dict) else {}
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    text = artifact_text(artifacts, artifact_id)
    if text is None:
        return None
    card = next((c for c in _artifact_cards(case_id, state, snap.get("events") or []) if c["artifact_id"] == artifact_id), None)
    filename = (card or {}).get("filename") or artifact_id
    mime = (card or {}).get("mime_type") or "text/plain; charset=utf-8"
    return filename, text.encode("utf-8"), mime


@router.get("/cases/{case_id}/artifacts/{artifact_id}")
async def get_artifact(case_id: str, artifact_id: str):
    if control_plane.get_case(case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")
    inline = _inline_artifact(case_id, artifact_id)
    if inline is not None:
        filename, content, mime = inline
    elif artifact_store.configured():
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
        headers={"Content-Disposition": content_disposition(filename)},
    )
