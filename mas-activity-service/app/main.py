"""In-memory MAS activity feed for chat-style handoff presentation + HITL."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.enrich import enrich_turn
from app.orchestrator import BinaryMap, OrchestratorError, hitl_backend, invoke_orchestrator
from app import knowledge as knowledge_store
from app.durable import durable_enabled, fetch_task_feed, fetch_task_list
from app.persist import load_state, persist_enabled, save_state

STATIC = Path(__file__).resolve().parents[1] / "static"
ACTIVITY_KEY = os.getenv("MAS_ACTIVITY_KEY", "dev-local")
MAX_TURNS_PER_TASK = 500
MAX_TASKS = 200
MAX_BODY_BYTES = 256_000
MAX_START_BODY_BYTES = 12_000_000
MAX_START_FILE_BYTES = 2_097_152
MAX_START_FILES = 40
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
HITL_ACTIONS = frozenset({"status", "reply", "approve", "reject", "cancel"})
ANON = frozenset({"", "anonymous", "anon", "n/a", "na", "unknown"})
SCHEDULE_EXT = re.compile(r"\.(?:data|inc|sch|txt|grdecl)$", re.I)
EXCEL_EXT = re.compile(r"\.(?:xlsx|xls)$", re.I)
TRAJECTORY_EXT = re.compile(r"\.dev$", re.I)
SURFACE_EXT = re.compile(r"\.(?:cps3|grd|grid|txt)$", re.I)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks, order = load_state()
    _tasks.clear()
    _order.clear()
    _subscribers.clear()
    _tasks.update(tasks)
    _order.extend(order)
    yield


app = FastAPI(
    title="MAS Activity Service",
    version="0.5.1",
    description="Live handoff transcript + HITL + Data Table hydrate for Petroleum Engineering MAS.",
    lifespan=lifespan,
)


class TurnIn(BaseModel):
    at: str | None = None
    stage: str | None = None
    status: str | None = None
    summary: str | None = None
    brief: str | None = None
    duration_ms: int | None = None
    from_specialist: str | None = None
    to_specialist: str | None = None
    from_role: str | None = None
    to_role: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    event_type: str = "handoff"


class TurnPost(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    trace_id: str | None = None
    turn: TurnIn

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_RE.match(value):
            raise ValueError("task_id has invalid characters")
        return value


class EventIn(BaseModel):
    event_type: str | None = None
    stage: str | None = None
    status: str | None = None
    summary: str | None = None
    brief: str | None = None
    duration_ms: int | None = None
    actor: str | None = None
    at: str | None = None
    handoff: dict[str, Any] | None = None
    task_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    human_gate: dict[str, Any] | None = None


class SyncPost(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    trace_id: str | None = None
    status: str | None = None
    version: int | None = None
    human_gate: dict[str, Any] | None = None
    events: list[EventIn] = Field(default_factory=list)
    turns: list[TurnIn] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_RE.match(value):
            raise ValueError("task_id has invalid characters")
        return value


class HitlPost(BaseModel):
    action: Literal["reply", "approve", "reject", "cancel", "status"] = "reply"
    human_response: str | None = None
    requested_by: str = Field(min_length=1, max_length=120)
    gate_id: str | None = None
    expected_version: int | None = None


class KnowledgeDocumentPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1)
    keywords: list[str] | None = None
    topics: list[str] | None = None
    task_patterns: list[str] | None = None


class KnowledgeDocumentCreate(BaseModel):
    target_base: str = Field(min_length=1, max_length=120)
    knowledge_id: str = Field(min_length=2, max_length=119)
    knowledge_type: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    task_patterns: list[str] = Field(default_factory=list)
    author: str | None = Field(default=None, max_length=120)


class TaskMeta(BaseModel):
    task_id: str
    title: str | None = None
    updated_at: str
    turn_count: int | None = None
    last_status: str | None = None
    last_at_abs: str | None = None
    status: str | None = None
    awaiting_human: bool = False


_lock = asyncio.Lock()
_tasks: dict[str, dict[str, Any]] = {}
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_order: list[str] = []
_started_at = datetime.now(timezone.utc).isoformat()
# One-shot empty-rail list pull so inactive webhooks cannot spam every GET /v1/tasks.
_empty_rail_list_attempted = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_store() -> None:
    """Test helper — clears in-memory state."""
    global _empty_rail_list_attempted
    _tasks.clear()
    _subscribers.clear()
    _order.clear()
    _empty_rail_list_attempted = False
    _persist()


def _persist() -> None:
    save_state(_tasks, _order)


def _require_key(x_activity_key: str | None = Header(default=None, alias="X-Activity-Key")) -> None:
    if not secrets.compare_digest(str(x_activity_key or ""), ACTIVITY_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Activity-Key")


def _new_task_shell(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": None,
        "objective": None,
        "updated_at": _now(),
        "turns": [],
        "status": None,
        "version": None,
        "gate": None,
        "transcript_loaded": False,
    }


def _is_local_presentation_task(task_id: str) -> bool:
    """Local Activity starts / demo seeds are not CAS catalog rows."""
    return task_id.startswith("act_") or task_id.startswith("demo_")


def _trim_to_max_tasks() -> None:
    """Evict oldest entries, preferring CAS/catalog ids so act_*/demo_* stay on the rail."""
    while len(_order) > MAX_TASKS:
        evict_at = next(
            (i for i, tid in enumerate(_order) if not _is_local_presentation_task(tid)),
            0,
        )
        old = _order.pop(evict_at)
        _tasks.pop(old, None)
        _subscribers.pop(old, None)


def _ensure_task(task_id: str) -> dict[str, Any]:
    """Create a task shell without MAX_TASKS eviction (caller must trim)."""
    if task_id not in _tasks:
        _tasks[task_id] = _new_task_shell(task_id)
        _order.append(task_id)
    return _tasks[task_id]


def _touch(task_id: str) -> dict[str, Any]:
    _ensure_task(task_id)
    _trim_to_max_tasks()
    return _tasks[task_id]


def _set_objective(task: dict[str, Any], objective: str | None) -> None:
    """Store objective from CAS/start. Non-empty incoming always wins (CAS is authoritative)."""
    text = str(objective or "").strip()
    if not text:
        return
    task["objective"] = text[:8000]


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _merge_updated_at(task: dict[str, Any], incoming: str | None) -> None:
    """Keep the newer of local vs CAS timestamps so hydrate cannot demote live tasks."""
    incoming_ts = _parse_ts(incoming)
    if incoming_ts is None:
        return
    local_ts = _parse_ts(task.get("updated_at"))
    if local_ts is None or incoming_ts >= local_ts:
        task["updated_at"] = incoming_ts.isoformat()


def _merge_version(task: dict[str, Any], version: Any) -> None:
    """Keep the higher of local vs CAS version so hydrate cannot regress HITL expected_version."""
    if not isinstance(version, int):
        return
    prior = task.get("version")
    if not isinstance(prior, int) or version >= prior:
        task["version"] = version


def _task_objective(task: dict[str, Any]) -> str | None:
    direct = str(task.get("objective") or "").strip()
    if direct:
        return direct
    for turn in task.get("turns") or []:
        details = turn.get("details") if isinstance(turn.get("details"), dict) else {}
        for key in ("objective", "task_description", "request_text", "problem_statement"):
            val = details.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:8000]
    return None


def _normalize_turn(raw: TurnIn | dict[str, Any], *, trace_id: str | None = None) -> dict[str, Any]:
    if isinstance(raw, TurnIn):
        data = raw.model_dump()
    else:
        data = dict(raw)
    data["trace_id"] = trace_id or data.get("trace_id")
    return enrich_turn(data, received_at=_now())


def _gate_payload(task: dict[str, Any]) -> dict[str, Any] | None:
    gate = task.get("gate")
    if not isinstance(gate, dict) or not gate:
        return None
    return dict(gate)


def _is_awaiting_status(status: Any) -> bool:
    """Orchestrator uses awaiting_human; Trace Writer / handoffs often emit AWAITING_HUMAN."""
    return str(status or "").strip().lower() == "awaiting_human"


def _normalize_task_status(status: Any) -> str | None:
    if status is None:
        return None
    text = str(status).strip()
    if not text:
        return None
    return "awaiting_human" if _is_awaiting_status(text) else text


def _awaiting(task: dict[str, Any]) -> bool:
    return _is_awaiting_status(task.get("status")) and bool(_gate_payload(task))


def _turn_fingerprint(turn: dict[str, Any]) -> tuple[Any, ...]:
    """Stable key so Trace sync after durable hydrate does not duplicate handoffs."""
    frm = turn.get("from") if isinstance(turn.get("from"), dict) else {}
    details = turn.get("details") if isinstance(turn.get("details"), dict) else {}
    event_id = details.get("event_id") or turn.get("event_id")
    return (
        str(event_id or ""),
        str(turn.get("trace_id") or ""),
        str(turn.get("at") or ""),
        str(turn.get("status") or ""),
        str(turn.get("summary") or turn.get("text") or "")[:240],
        str(turn.get("brief") or "")[:240],
        str(frm.get("role") or turn.get("from_role") or ""),
        str(turn.get("stage") or ""),
    )


async def _publish(task_id: str, message: dict[str, Any]) -> None:
    queues = list(_subscribers.get(task_id, []))
    for queue in queues:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


async def _append_turns(
    task_id: str,
    turns: list[dict[str, Any]],
    *,
    title: str | None = None,
    objective: str | None = None,
    replace: bool = False,
    source: str = "live",
) -> list[dict[str, Any]]:
    """Append turns. source='cas' for Data Table hydrate; 'live' for Trace/SSE/HITL.

    replace=True clears prior CAS-sourced turns, then merges incoming CAS rows and
    keeps live-only turns so durable refresh cannot wipe fresher Trace Writer sync.
    """
    async with _lock:
        task = _touch(task_id)
        live_keep: list[dict[str, Any]] = []
        if replace:
            live_keep = [
                dict(t)
                for t in task["turns"]
                if isinstance(t, dict) and str(t.get("source") or "") == "live"
            ]
            task["turns"] = []
        if title:
            task["title"] = title
        _set_objective(task, objective)
        existing = {_turn_fingerprint(t) for t in task["turns"] if isinstance(t, dict)}
        stored = []
        for turn in turns:
            turn = dict(turn)
            turn["source"] = source
            key = _turn_fingerprint(turn)
            if key in existing:
                continue
            existing.add(key)
            turn["turn_id"] = len(task["turns"]) + 1
            task["turns"].append(turn)
            stored.append(turn)
        if replace and live_keep:
            for turn in live_keep:
                key = _turn_fingerprint(turn)
                if key in existing:
                    continue
                existing.add(key)
                turn["source"] = "live"
                turn["turn_id"] = len(task["turns"]) + 1
                task["turns"].append(turn)
            task["turns"].sort(key=lambda t: str(t.get("at") or ""))
            for index, item in enumerate(task["turns"], start=1):
                item["turn_id"] = index
        if len(task["turns"]) > MAX_TURNS_PER_TASK:
            task["turns"] = task["turns"][-MAX_TURNS_PER_TASK :]
            for index, item in enumerate(task["turns"], start=1):
                item["turn_id"] = index
        task["transcript_loaded"] = True
        if stored or title or objective or replace:
            task["updated_at"] = _now()
        _persist()
    for turn in stored:
        await _publish(task_id, {"type": "turn", "task_id": task_id, "turn": turn})
    return stored


async def _set_gate(
    task_id: str,
    *,
    status: str | None = None,
    version: int | None = None,
    gate: dict[str, Any] | None = None,
    clear_gate: bool = False,
) -> dict[str, Any]:
    async with _lock:
        task = _touch(task_id)
        if status is not None:
            task["status"] = _normalize_task_status(status) or status
        if version is not None:
            _merge_version(task, version)
        if clear_gate:
            task["gate"] = None
        elif gate is not None:
            task["gate"] = dict(gate)
        task["updated_at"] = _now()
        snapshot = {
            "status": task.get("status"),
            "version": task.get("version"),
            "human_gate": _gate_payload(task),
            "awaiting_human": _awaiting(task),
            "updated_at": task["updated_at"],
        }
        _persist()
    await _publish(
        task_id,
        {
            "type": "gate",
            "task_id": task_id,
            **snapshot,
        },
    )
    return snapshot


def _events_to_turns(events: list[EventIn], *, trace_id: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        if (event.event_type or "").lower() not in {"handoff", "mas_activity_turn"}:
            continue
        handoff = event.handoff or {}
        details = handoff.get("details") if isinstance(handoff.get("details"), dict) else {}
        if event.duration_ms is not None:
            details = {**details, "duration_ms": event.duration_ms}
        if event.event_id:
            details = {**details, "event_id": event.event_id}
        out.append(
            _normalize_turn(
                {
                    "at": event.at,
                    "stage": event.stage,
                    "status": event.status,
                    "summary": event.summary,
                    "brief": event.brief or handoff.get("brief"),
                    "duration_ms": event.duration_ms if event.duration_ms is not None else details.get("duration_ms"),
                    "from_specialist": handoff.get("from_specialist"),
                    "to_specialist": handoff.get("to_specialist"),
                    "from_role": handoff.get("from_role") or event.actor,
                    "to_role": handoff.get("to_role"),
                    "details": details,
                    "event_type": "handoff",
                    "trace_id": event.trace_id or trace_id,
                },
                trace_id=trace_id,
            )
        )
    return out


def _human_turn(*, action: str, requested_by: str, human_response: str | None, gate: dict[str, Any] | None) -> dict[str, Any]:
    status_map = {
        "reply": "HUMAN_REPLY",
        "approve": "HUMAN_APPROVED",
        "reject": "HUMAN_REJECTED",
        "cancel": "HUMAN_REJECTED",
    }
    status = status_map.get(action, "HUMAN_REPLY")
    summary = {
        "reply": f"{requested_by} replied to human gate.",
        "approve": f"{requested_by} approved the gate result.",
        "reject": f"{requested_by} rejected the gate result.",
        "cancel": f"{requested_by} cancelled the task.",
    }.get(action, f"{requested_by} submitted HITL action.")
    brief = {
        "reply": f"{requested_by} отправил инструкции в HITL. Оркестратор продолжит с этим ответом.",
        "approve": f"{requested_by} утвердил результат. Задача возобновляется как approve.",
        "reject": f"{requested_by} отклонил результат. Релиз не выдаётся.",
        "cancel": f"{requested_by} отменил задачу на HITL-gate.",
    }.get(action, summary)
    details: dict[str, Any] = {
        "action": action,
        "requested_by": requested_by,
        "gate_id": (gate or {}).get("gate_id"),
        "gate_kind": (gate or {}).get("kind"),
    }
    if human_response:
        details["fields"] = human_response[:120]
    return _normalize_turn(
        {
            "status": status,
            "stage": "hitl",
            "summary": summary,
            "brief": brief if not human_response else f"{brief} «{human_response[:240]}»",
            "from_role": requested_by,
            "to_role": "Orchestrator",
            "from_specialist": "human_operator",
            "to_specialist": "universal_orchestrator",
            "details": details,
            "event_type": "hitl",
        }
    )


def _local_apply_hitl(
    task: dict[str, Any],
    *,
    action: str,
    human_response: str | None,
    requested_by: str,
) -> dict[str, Any]:
    gate = _gate_payload(task) or {}
    version = int(task.get("version") or gate.get("expected_version") or 1)
    if action == "status":
        return {
            "contract": "orchestrator_response",
            "contract_version": "1.0",
            "task_id": task["task_id"],
            "version": version,
            "status": task.get("status") or "unknown",
            "message": "Local activity status.",
            "human_gate": gate or None,
            "next_action": "resume_with_task_id_expected_version_gate_id_and_action" if _awaiting(task) else None,
            "backend": "local",
        }
    if not _awaiting(task):
        return {
            "contract": "orchestrator_response",
            "contract_version": "1.0",
            "task_id": task["task_id"],
            "version": version,
            "status": task.get("status") or "conflict",
            "message": "Task is not awaiting human input.",
            "human_gate": gate or None,
            "backend": "local",
        }
    if action == "reply":
        status = "planning"
        message = "Local HITL reply accepted; gate closed for demo resume."
    elif action == "approve":
        status = "completed"
        message = "Local HITL approve accepted."
    elif action == "reject":
        status = "rejected"
        message = "Local HITL reject accepted."
    else:
        status = "cancelled"
        message = "Local HITL cancel accepted."
    return {
        "contract": "orchestrator_response",
        "contract_version": "1.0",
        "task_id": task["task_id"],
        "version": version + 1,
        "status": status,
        "message": message,
        "human_gate": None,
        "result": {"action": action, "requested_by": requested_by, "human_response": human_response},
        "backend": "local",
    }


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"} and request.url.path.startswith("/v1/"):
        content_length = request.headers.get("content-length")
        limit = MAX_START_BODY_BYTES if request.url.path.rstrip("/") == "/v1/tasks/start" else MAX_BODY_BYTES
        if content_length and content_length.isdigit() and int(content_length) > limit:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/v1/") else response.headers.get("Cache-Control", "")
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "mas-activity",
        "version": "0.5.1",
        "hitl_backend": hitl_backend(),
        "durable_hydrate": durable_enabled(),
        "state_persist": persist_enabled(),
        "tasks": len(_tasks),
        "time": _now(),
        "started_at": _started_at,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    if not STATIC.exists() or not (STATIC / "index.html").exists():
        raise HTTPException(status_code=503, detail="UI assets missing")
    return {"status": "ready", "hitl_backend": hitl_backend(), "durable_hydrate": durable_enabled()}


@app.post("/v1/turns")
async def post_turn(body: TurnPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    turn = _normalize_turn(body.turn, trace_id=body.trace_id)
    stored = await _append_turns(body.task_id, [turn])
    return {"stored": True, "task_id": body.task_id, "turn": stored[0]}


async def _ingest_sync(
    body: SyncPost,
    *,
    title: str | None = None,
    objective: str | None = None,
    replace_turns: bool = False,
) -> dict[str, Any]:
    turns = [_normalize_turn(t, trace_id=body.trace_id) for t in body.turns]
    turns.extend(_events_to_turns(body.events, trace_id=body.trace_id))
    gate = body.human_gate if isinstance(body.human_gate, dict) else None
    gate_ok = bool(gate and gate.get("gate_id"))
    if gate and not gate_ok:
        gate = None
    explicit_status = body.status is not None
    status = body.status
    for event in body.events:
        event_gate = event.human_gate if isinstance(event.human_gate, dict) else None
        if event_gate and event_gate.get("gate_id") and not gate:
            gate = event_gate
            gate_ok = True
        if event.status and status is None and (event_gate or _is_awaiting_status(event.status)):
            status = event.status
    if gate_ok and status is None:
        status = "awaiting_human"
    status = _normalize_task_status(status)
    # Durable feed: CAS gate is authoritative (omit → clear). Never leave orphan awaiting.
    if replace_turns and _is_awaiting_status(status) and not gate_ok:
        status = "running"
    if gate_ok or explicit_status or body.version is not None or replace_turns:
        clear_gate = (not gate_ok) if replace_turns else (
            gate is None and explicit_status and not _is_awaiting_status(status)
        )
        await _set_gate(
            body.task_id,
            status=status if (gate_ok or explicit_status or replace_turns) else None,
            version=body.version,
            gate=gate if gate_ok else None,
            clear_gate=clear_gate,
        )
    stored: list[dict[str, Any]] = []
    if turns or replace_turns:
        stored = await _append_turns(
            body.task_id,
            turns,
            title=title,
            objective=objective,
            replace=replace_turns,
            source="cas" if replace_turns else "live",
        )
    else:
        async with _lock:
            task = _touch(body.task_id)
            if title:
                task["title"] = title
            _set_objective(task, objective)
            if title or objective:
                task["updated_at"] = _now()
            _persist()
    return {
        "stored": bool(stored) or bool(title) or bool(objective) or gate_ok or explicit_status or replace_turns,
        "task_id": body.task_id,
        "count": len(stored),
        "turns": stored,
    }


@app.post("/v1/sync")
async def post_sync(body: SyncPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    result = await _ingest_sync(body)
    if not result["stored"] and result["count"] == 0 and not body.events and not body.turns:
        result["reason"] = "no_handoff_events"
    return result


def _catalog_rows(limit: int) -> list[dict[str, Any]]:
    rows = []
    for task_id in _order:
        task = _tasks.get(task_id)
        if not task:
            continue
        last = task["turns"][-1] if task["turns"] else {}
        loaded = bool(task.get("transcript_loaded")) or bool(task["turns"])
        rows.append(
            TaskMeta(
                task_id=task_id,
                title=task.get("title"),
                updated_at=task["updated_at"],
                turn_count=len(task["turns"]) if loaded else None,
                last_status=last.get("status") or task.get("status"),
                last_at_abs=last.get("at_abs"),
                status=task.get("status"),
                awaiting_human=_awaiting(task),
            ).model_dump()
        )
    # Newest first regardless of _touch append order (list hydrate is newest-first).
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return rows[:limit]


def _list_catalog_complete(payload: dict[str, Any], returned_n: int) -> bool:
    """True when list hydrate includes the full CAS catalog (safe to prune ghosts).

    List webhook caps at 200 newest rows but reports total in ``count``. A truncated
    page must not evict older in-memory tasks that still exist in Data Tables.
    """
    raw = payload.get("count")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return False
    total = int(raw)
    if total < 0:
        return False
    return total <= returned_n


async def _apply_list_hydrate(payload: dict[str, Any], *, prune_missing: bool = False) -> dict[str, Any]:
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    applied = 0
    do_prune = False
    catalog_complete = False
    async with _lock:
        touched: list[str] = []
        for raw in tasks:
            if not isinstance(raw, dict):
                continue
            task_id = str(raw.get("task_id") or "").strip()
            if not TASK_ID_RE.match(task_id):
                continue
            # No mid-loop MAX_TASKS eviction — trim once after reorder/prune.
            task = _ensure_task(task_id)
            title = str(raw.get("title") or "").strip() or None
            if title:
                task["title"] = title
            objective = str(raw.get("objective") or "").strip() or None
            _set_objective(task, objective)
            status = _normalize_task_status(raw.get("status"))
            gate = raw.get("human_gate") if isinstance(raw.get("human_gate"), dict) else None
            gate_ok = bool(gate and gate.get("gate_id"))
            # Orphan awaiting (status says HITL, no gate_id) → do not arm the composer.
            if status and _is_awaiting_status(status) and not gate_ok:
                status = "running"
            if status:
                task["status"] = status
            _merge_version(task, raw.get("version"))
            updated = str(raw.get("updated_at") or "").strip() or None
            _merge_updated_at(task, updated)
            if gate_ok:
                task["gate"] = dict(gate)
            elif status and not _is_awaiting_status(status):
                task["gate"] = None
            elif raw.get("awaiting_human") is False:
                task["gate"] = None
            touched.append(task_id)
            applied += 1
        touched_set = set(touched)
        catalog_complete = _list_catalog_complete(payload, len(touched))
        # Only prune when the webhook returned a complete catalog (count ≤ returned rows).
        do_prune = bool(prune_missing and catalog_complete)
        if do_prune:
            # Drop CAS ghosts missing from catalog; keep local act_*/demo_* presentation tasks.
            for tid in list(_tasks):
                if tid not in touched_set and not _is_local_presentation_task(tid):
                    _tasks.pop(tid, None)
                    _subscribers.pop(tid, None)
            local_rest = [tid for tid in _order if tid in _tasks and tid not in touched_set]
            # CAS oldest→newest first; locals last so MAX_TASKS trim prefers dropping old CAS.
            cas_order = list(reversed(touched)) if touched else []
            _order[:] = cas_order + local_rest
        elif touched:
            rest = [tid for tid in _order if tid not in touched_set]
            local_rest = [tid for tid in rest if _is_local_presentation_task(tid)]
            other_rest = [tid for tid in rest if not _is_local_presentation_task(tid)]
            _order[:] = other_rest + list(reversed(touched)) + local_rest
        _trim_to_max_tasks()
        _persist()
    return {"applied": applied, "pruned": do_prune, "catalog_complete": catalog_complete}


async def _apply_feed_hydrate(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok", True):
        return {"stored": False, "reason": payload.get("error") or "hydrate_failed"}
    task_id = str(payload.get("task_id") or "").strip()
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id in hydrate payload")
    events_raw = payload.get("events") if isinstance(payload.get("events"), list) else []
    events: list[EventIn] = []
    for item in events_raw:
        if isinstance(item, EventIn):
            events.append(item)
        elif isinstance(item, dict):
            try:
                events.append(EventIn.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
    body = SyncPost(
        task_id=task_id,
        status=payload.get("status"),
        version=payload.get("version") if isinstance(payload.get("version"), int) else None,
        human_gate=payload.get("human_gate") if isinstance(payload.get("human_gate"), dict) else None,
        events=events,
    )
    title = str(payload.get("title") or "").strip() or None
    objective = str(payload.get("objective") or "").strip() or None
    # Replace transcript atomically inside _ingest_sync/_append_turns (no empty window on failure).
    return await _ingest_sync(body, title=title, objective=objective, replace_turns=True)


@app.post("/v1/hydrate")
async def post_hydrate(request: Request, _: None = Depends(_require_key)) -> dict[str, Any]:
    """Accept list and/or feed payloads from n8n Data Table hydrate workflows."""
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    result: dict[str, Any] = {"ok": True}
    if isinstance(raw.get("tasks"), list) or raw.get("contract") == "mas_activity_task_list":
        result["list_applied"] = (await _apply_list_hydrate(raw))["applied"]
    if raw.get("task_id") and (raw.get("events") is not None or raw.get("contract") == "mas_activity_feed_hydrate"):
        result["feed"] = await _apply_feed_hydrate(raw)
    return result


@app.get("/v1/tasks")
async def list_tasks(
    limit: int = Query(default=30, ge=1, le=200),
    durable: bool = Query(default=False),
) -> dict[str, Any]:
    global _empty_rail_list_attempted
    hydrate_meta = None
    auto_empty = (
        not durable
        and not _order
        and durable_enabled()
        and not _empty_rail_list_attempted
    )
    if durable or auto_empty:
        if auto_empty:
            _empty_rail_list_attempted = True
        try:
            payload = await fetch_task_list()
            if payload:
                # Prune ghosts only on explicit durable refresh when the list page is complete
                # (list WF returns at most 200 newest; truncated pages must not evict older CAS).
                applied_meta = await _apply_list_hydrate(payload, prune_missing=durable)
                hydrate_meta = {
                    "applied": applied_meta["applied"],
                    "pruned": applied_meta["pruned"],
                    "catalog_complete": applied_meta["catalog_complete"],
                    "source": payload.get("source"),
                    "auto_empty": auto_empty,
                }
        except Exception as exc:  # noqa: BLE001
            hydrate_meta = {"error": str(exc)[:300], "auto_empty": auto_empty}
    rows = _catalog_rows(limit)
    out: dict[str, Any] = {"tasks": rows, "durable_hydrate": durable_enabled()}
    if hydrate_meta is not None:
        out["hydrate"] = hydrate_meta
    return out


def _task_feed(task_id: str) -> dict[str, Any]:
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "contract": "mas_activity_feed",
        "contract_version": "1.1",
        "task_id": task_id,
        "title": task.get("title"),
        "objective": _task_objective(task),
        "updated_at": task["updated_at"],
        "status": task.get("status"),
        "version": task.get("version"),
        "awaiting_human": _awaiting(task),
        "human_gate": _gate_payload(task),
        "hitl_backend": hitl_backend(),
        "durable_hydrate": durable_enabled(),
        "activity": task["turns"],
    }


@app.get("/v1/tasks/{task_id}")
async def get_task(
    task_id: str,
    durable: bool = Query(default=False),
) -> dict[str, Any]:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    task = _tasks.get(task_id)
    need = durable or task is None or not task.get("turns")
    hydrate_meta: dict[str, Any] | None = None
    if need and durable_enabled():
        try:
            payload = await fetch_task_feed(task_id)
            if payload:
                result = await _apply_feed_hydrate(payload)
                if isinstance(result, dict) and result.get("stored") is False:
                    reason = str(result.get("reason") or "hydrate_failed")
                    if _tasks.get(task_id) is None:
                        raise HTTPException(status_code=404, detail="Task not found")
                    if durable:
                        hydrate_meta = {"ok": False, "error": reason}
                elif isinstance(result, dict):
                    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
                    hydrate_meta = {
                        "ok": True,
                        "truncated": bool(src.get("truncated")),
                        "trace_rows": src.get("trace_rows"),
                        "handoff_events": src.get("handoff_events"),
                    }
            elif durable and task is None:
                raise HTTPException(status_code=404, detail="Task not found")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            if task is None:
                raise HTTPException(status_code=502, detail=f"Durable hydrate failed: {exc}") from exc
            if durable:
                hydrate_meta = {"ok": False, "error": str(exc)[:300]}
    out = _task_feed(task_id)
    if hydrate_meta is not None:
        out["hydrate"] = hydrate_meta
    return out


@app.get("/v1/tasks/{task_id}/gate")
async def get_gate(task_id: str, refresh: bool = Query(default=False)) -> dict[str, Any]:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    task = _tasks.get(task_id)
    if not task and not refresh:
        if durable_enabled():
            try:
                payload = await fetch_task_feed(task_id)
                if payload:
                    await _apply_feed_hydrate(payload)
                    task = _tasks.get(task_id)
            except Exception:  # noqa: BLE001
                pass
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    backend = hitl_backend()
    orch: dict[str, Any] | None = None
    if refresh and backend != "local":
        try:
            orch = await invoke_orchestrator(
                {"action": "status", "task_id": task_id, "requested_by": "mas-activity"}
            )
        except OrchestratorError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        await _set_gate(
            task_id,
            status=orch.get("status"),
            version=orch.get("version"),
            gate=orch.get("human_gate") if isinstance(orch.get("human_gate"), dict) else None,
            clear_gate=not orch.get("human_gate"),
        )
        task = _tasks[task_id]
    elif not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "task_id": task_id,
        "status": task.get("status"),
        "version": task.get("version"),
        "awaiting_human": _awaiting(task),
        "human_gate": _gate_payload(task),
        "hitl_backend": backend,
        "orchestrator": orch,
    }


def _local_start_response(
    *,
    task_id: str,
    description: str,
    requested_by: str,
    file_names: list[str],
) -> dict[str, Any]:
    gate = {
        "gate_id": f"gate_{task_id}_local_start",
        "kind": "needs_input",
        "reason": (
            "Локальный режим Activity: задача создана в морде без Orchestrator. "
            "Для реального MAS-запуска настройте HITL_MODE=webhook|n8n_rest и credentials."
        ),
        "questions": [
            {
                "id": "objective",
                "text": "Проверьте формулировку и вложения, затем перезапустите со live backend.",
                "expected_format": "свободный текст",
                "required": False,
            }
        ],
        "expected_version": 1,
    }
    return {
        "contract": "orchestrator_response",
        "contract_version": "1.0",
        "task_id": task_id,
        "version": 1,
        "status": "awaiting_human",
        "message": "Local Activity start (presentation only).",
        "human_gate": gate,
        "result": {
            "action": "start",
            "requested_by": requested_by,
            "objective": description[:500],
            "files": file_names,
            "local": True,
        },
        "backend": "local",
    }


def _start_turn(*, requested_by: str, description: str, file_names: list[str], backend: str) -> dict[str, Any]:
    files_note = f" Вложений: {len(file_names)}." if file_names else ""
    brief = (
        f"{requested_by} создал новую задачу из Activity.{files_note} "
        + (
            "Локальный demo-start — Orchestrator не вызван."
            if backend == "local"
            else "Запрос передан Orchestrator (action=start)."
        )
    )
    return _normalize_turn(
        {
            "status": "TASK_STARTED",
            "stage": "intake",
            "summary": f"{requested_by} started a new MAS task.",
            "brief": brief,
            "from_role": requested_by,
            "to_role": "Orchestrator",
            "from_specialist": "human_operator",
            "to_specialist": "universal_orchestrator",
            "details": {
                "action": "start",
                "requested_by": requested_by,
                "objective": description[:4000],
                "file_count": len(file_names),
                "files": ", ".join(file_names[:12]) if file_names else None,
                "backend": backend,
            },
            "event_type": "hitl",
        }
    )


async def _read_upload(upload: UploadFile, *, field: str) -> tuple[str, bytes, str]:
    filename = (upload.filename or field).strip() or field
    raw = await upload.read(MAX_START_FILE_BYTES + 1)
    if len(raw) > MAX_START_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_START_FILE_BYTES} bytes): {filename}",
        )
    mime = (upload.content_type or "application/octet-stream").strip()
    return filename, raw, mime


def _assign_upload_key(field: str, filename: str, counters: dict[str, int]) -> str:
    """Map multipart fields to Orchestrator/Entry binary keys."""
    name = filename.lower()
    if field == "file" or EXCEL_EXT.search(name):
        return "file"
    if field == "surface_file" or (SURFACE_EXT.search(name) and not SCHEDULE_EXT.search(name)):
        return "surface_file"
    if field.startswith("trajectory") or TRAJECTORY_EXT.search(name):
        n = counters.get("trajectory", 0)
        counters["trajectory"] = n + 1
        return "trajectory_files" if n == 0 else f"trajectory_files{n}"
    if field.startswith("schedule") or SCHEDULE_EXT.search(name):
        n = counters.get("schedule", 0)
        counters["schedule"] = n + 1
        return "schedule_files" if n == 0 else f"schedule_files{n}"
    # Default: treat unknown as schedule fragment if text-like, else reject later via count
    n = counters.get("schedule", 0)
    counters["schedule"] = n + 1
    return "schedule_files" if n == 0 else f"schedule_files{n}"


@app.post("/v1/tasks/start")
async def post_start_task(
    _: None = Depends(_require_key),
    task_description: str = Form(...),
    requested_by: str = Form(...),
    schedule_root: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    surface_file: UploadFile | None = File(default=None),
    schedule_files: list[UploadFile] | None = File(default=None),
    trajectory_files: list[UploadFile] | None = File(default=None),
    attachments: list[UploadFile] | None = File(default=None),
) -> dict[str, Any]:
    """Start a new Orchestrator task (Entry-shaped). Multipart mirrors Form — MAS Entry."""
    description = (task_description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="task_description is required")
    by = (requested_by or "").strip()
    if by.lower() in ANON:
        raise HTTPException(status_code=400, detail="requested_by must be a named engineer (not anonymous)")
    root = (schedule_root or "").strip()

    binary: BinaryMap = {}
    counters: dict[str, int] = {}
    file_names: list[str] = []

    async def take(upload: UploadFile | None, field_hint: str) -> None:
        nonlocal binary, file_names
        if upload is None or not (upload.filename or "").strip():
            return
        if len(binary) >= MAX_START_FILES:
            raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_START_FILES})")
        filename, content, mime = await _read_upload(upload, field=field_hint)
        key = _assign_upload_key(field_hint, filename, counters)
        if key == "file" and "file" in binary:
            raise HTTPException(status_code=400, detail="Only one Excel workbook (file) allowed")
        if key == "surface_file" and "surface_file" in binary:
            raise HTTPException(status_code=400, detail="Only one surface_file allowed")
        binary[key] = (filename, content, mime)
        file_names.append(filename)

    await take(file, "file")
    await take(surface_file, "surface_file")
    for item in schedule_files or []:
        await take(item, "schedule_files")
    for item in trajectory_files or []:
        await take(item, "trajectory_files")
    for item in attachments or []:
        await take(item, "attachments")

    input_files = [
        {"field": key, "filename": name, "mime_type": mime}
        for key, (name, _content, mime) in binary.items()
    ]
    request: dict[str, Any] = {
        "objective": description,
        "problem_statement": description,
        "task_description": description,
        "input_files": input_files,
    }
    if root:
        request["schedule_root"] = root
    if any(k.startswith("schedule_file") for k in binary):
        request["build_mode"] = "AUTO"

    client_task_id = f"act_{int(time.time() * 1000):x}_{secrets.token_hex(4)}"
    payload = {
        "entrypoint": "activity_ui",
        "action": "start",
        "task_id": client_task_id,
        "task_description": description,
        "request_text": description,
        "request": request,
        "schedule_root": root or None,
        "context": {"source": "mas-activity-start", "submitted_task_id": client_task_id},
        "requested_by": by,
    }

    backend = hitl_backend()
    if backend == "local":
        orch = _local_start_response(
            task_id=client_task_id,
            description=description,
            requested_by=by,
            file_names=file_names,
        )
    else:
        try:
            orch = await invoke_orchestrator(payload, files=binary or None, timeout_s=180.0)
        except OrchestratorError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    task_id = str(orch.get("task_id") or client_task_id).strip()
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=502, detail="Orchestrator returned invalid task_id")

    title = description[:80] + ("…" if len(description) > 80 else "")
    turn = _start_turn(requested_by=by, description=description, file_names=file_names, backend=backend)
    await _append_turns(task_id, [turn], title=title, objective=description)

    gate = orch.get("human_gate") if isinstance(orch.get("human_gate"), dict) else None
    status = orch.get("status")
    version = orch.get("version")
    await _set_gate(
        task_id,
        status=status,
        version=version if isinstance(version, int) else None,
        gate=gate,
        clear_gate=not gate,
    )

    return {
        "ok": True,
        "task_id": task_id,
        "ui": f"/t/{task_id}",
        "backend": backend,
        "orchestrator": orch,
        "awaiting_human": _is_awaiting_status(status) and bool(gate and gate.get("gate_id")),
        "human_gate": gate,
        "turn": turn,
        "file_count": len(file_names),
    }


@app.post("/v1/tasks/{task_id}/hitl")
async def post_hitl(task_id: str, body: HitlPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    action = body.action.lower()
    if action not in HITL_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported action")
    requested_by = (body.requested_by or "").strip()
    if requested_by.lower() in ANON:
        raise HTTPException(status_code=400, detail="requested_by must be a named engineer (not anonymous)")
    human_response = (body.human_response or "").strip() or None
    if action == "reply" and not human_response:
        raise HTTPException(status_code=400, detail="human_response is required for reply")

    async with _lock:
        task = _touch(task_id)
        local_gate = _gate_payload(task)
        local_version = task.get("version") or (local_gate or {}).get("expected_version")
        snapshot = dict(task)

    backend = hitl_backend()
    orch: dict[str, Any]

    if backend == "local":
        orch = _local_apply_hitl(snapshot, action=action, human_response=human_response, requested_by=requested_by)
    else:
        try:
            status_payload = await invoke_orchestrator(
                {"action": "status", "task_id": task_id, "requested_by": requested_by}
            )
        except OrchestratorError as exc:
            # Live backends must fail closed: never pretend local apply succeeded.
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        else:
            gate = status_payload.get("human_gate") if isinstance(status_payload.get("human_gate"), dict) else None
            awaiting = _is_awaiting_status(status_payload.get("status")) and gate and gate.get("gate_id")
            await _set_gate(
                task_id,
                status=status_payload.get("status"),
                version=status_payload.get("version"),
                gate=gate,
                clear_gate=not gate,
            )
            if action == "status":
                return {
                    "ok": True,
                    "task_id": task_id,
                    "action": action,
                    "backend": backend,
                    "orchestrator": status_payload,
                    "awaiting_human": bool(awaiting),
                    "human_gate": gate,
                }
            if not awaiting:
                raise HTTPException(
                    status_code=409,
                    detail=status_payload.get("message") or f"Task status is {status_payload.get('status')}",
                )
            # Prefer fresh status/CAS over client-cached body fields.
            gate_id = str(gate.get("gate_id") or "").strip() or (body.gate_id or "").strip()
            expected_version = gate.get("expected_version")
            if expected_version is None:
                expected_version = status_payload.get("version")
            if expected_version is None:
                expected_version = body.expected_version
            try:
                expected_version = int(expected_version)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="expected_version must be an integer") from exc
            resume = {
                "action": action,
                "task_id": task_id,
                "expected_version": expected_version,
                "gate_id": gate_id,
                "human_response": human_response,
                "requested_by": requested_by,
            }
            try:
                orch = await invoke_orchestrator(resume)
            except OrchestratorError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if action != "status":
        turn = _human_turn(
            action=action,
            requested_by=requested_by,
            human_response=human_response,
            gate=local_gate or (orch.get("human_gate") if isinstance(orch.get("human_gate"), dict) else None),
        )
        stored = await _append_turns(task_id, [turn])
    else:
        stored = []

    clear = (not _is_awaiting_status(orch.get("status"))) or not orch.get("human_gate")
    gate_out = orch.get("human_gate") if isinstance(orch.get("human_gate"), dict) else None
    await _set_gate(
        task_id,
        status=orch.get("status"),
        version=orch.get("version"),
        gate=gate_out,
        clear_gate=clear,
    )

    return {
        "ok": True,
        "task_id": task_id,
        "action": action,
        "backend": orch.get("backend") or backend,
        "orchestrator": orch,
        "turn": stored[0] if stored else None,
        "awaiting_human": _is_awaiting_status(orch.get("status")) and bool(gate_out),
        "human_gate": gate_out,
        "local_version_hint": local_version,
    }


@app.get("/v1/tasks/{task_id}/stream")
async def stream_task(task_id: str) -> StreamingResponse:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers[task_id].append(queue)

    async def event_gen():
        try:
            task = _tasks.get(task_id)
            if task:
                payload = {
                    "type": "snapshot",
                    "task_id": task_id,
                    "title": task.get("title"),
                    "objective": _task_objective(task),
                    "activity": task["turns"],
                    "updated_at": task["updated_at"],
                    "status": task.get("status"),
                    "version": task.get("version"),
                    "awaiting_human": _awaiting(task),
                    "human_gate": _gate_payload(task),
                    "hitl_backend": hitl_backend(),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'snapshot', 'task_id': task_id, 'activity': [], 'awaiting_human': False, 'human_gate': None, 'objective': None})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if msg.get("type") == "turn":
                        yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping', 't': time.time()})}\n\n"
        finally:
            subs = _subscribers.get(task_id, [])
            if queue in subs:
                subs.remove(queue)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/v1/demo/seed")
async def seed_demo(_: None = Depends(_require_key)) -> dict[str, Any]:
    task_id = f"demo_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    base = datetime.now(timezone.utc)
    demo = [
        TurnIn(
            at=(base.replace(microsecond=0)).isoformat(),
            status="DELEGATED",
            stage="plan",
            summary="Оркестратор передаёт извлечение ORAT Excel Extractor.",
            brief=(
                "Оркестратор выбрал Excel Extractor, чтобы достать ORAT из workbook. "
                "Специалист получит только ограниченный пакет: цель и нужные поля."
            ),
            from_role="Orchestrator",
            to_role="Excel Extractor",
            from_specialist="universal_orchestrator",
            to_specialist="excel_extraction_specialist",
            details={"attempt": 1, "objective": "Extract ORAT for forecast WCONPROD"},
            duration_ms=None,
        ),
        TurnIn(
            at=(base.replace(microsecond=0)).isoformat(),
            status="EXCEL_EVIDENCE_READY",
            stage="excel",
            summary="Excel вернул 12 фактов для Schedule Builder.",
            brief=(
                "Excel Extractor закончил извлечение и собрал 12 фактов со snapshot. "
                "Пакет готов для Schedule Builder; workbook дальше не передаётся."
            ),
            from_role="Excel Extractor",
            to_role="Schedule Builder",
            from_specialist="excel_extraction_specialist",
            to_specialist="schedule_builder_specialist",
            details={"fact_count": 12, "source_snapshot_hash": "fnv1a32:demo01"},
            duration_ms=12400,
        ),
        TurnIn(
            at=(base.replace(microsecond=0)).isoformat(),
            status="SCHEDULE_EVIDENCE_GAP",
            stage="builder",
            summary="Schedule Builder requests 1 missing field(s) from Excel.",
            brief=(
                "Schedule Builder не может закрыть WCONPROD без BHP. "
                "Оркестратор отправит узкий Excel-запрос только по этому полю."
            ),
            from_role="Schedule Builder",
            to_role="Excel Extractor",
            from_specialist="schedule_builder_specialist",
            to_specialist="excel_extraction_specialist",
            details={"gap_count": 1, "fields": "BHP"},
            duration_ms=18250,
        ),
        TurnIn(
            at=(base.replace(microsecond=0)).isoformat(),
            status="RESUME_SCHEDULE",
            stage="excel",
            summary="Возврат в Schedule Builder с 13 фактами.",
            brief=(
                "Excel вернул недостающий BHP с тем же correlation_id. "
                "Schedule Builder продолжит на обновлённом пакете фактов."
            ),
            from_role="Excel Extractor",
            to_role="Schedule Builder",
            from_specialist="excel_extraction_specialist",
            to_specialist="schedule_builder_specialist",
            details={"fact_count": 13},
            duration_ms=4100,
        ),
        TurnIn(
            at=(base.replace(microsecond=0)).isoformat(),
            status="succeeded",
            stage="builder",
            summary="Черновик прогнозного schedule файла готов к утверждению человеком.",
            brief=(
                "Schedule Builder подготовил черновик прогнозного schedule файла и прошёл проверки. "
                "Выпуск остаётся за человеком — специалист сам себя не утверждает."
            ),
            from_role="Schedule Builder",
            to_role="Orchestrator",
            from_specialist="schedule_builder_specialist",
            to_specialist="universal_orchestrator",
            details={"release_ready": True},
            duration_ms=22100,
        ),
        TurnIn(
            at=(base.replace(microsecond=0)).isoformat(),
            status="AWAITING_HUMAN",
            stage="hitl",
            summary="Нужно утвердить, отклонить или уточнить условия выпуска.",
            brief=(
                "Черновик прогнозного schedule файла готов. Утвердите его, отклоните или напишите "
                "условия прямо в чате — без копирования gate_id и version."
            ),
            from_role="Orchestrator",
            to_role="Human",
            from_specialist="universal_orchestrator",
            to_specialist="human_operator",
            details={"gate_kind": "needs_approval", "release_ready": True},
            duration_ms=None,
        ),
    ]
    stored = await _append_turns(
        task_id,
        [_normalize_turn(t) for t in demo],
        title="Demo presentation run",
        objective=(
            "REVISE прогнозных WCONPROD: извлечь ORAT/BHP из Excel и выпустить "
            "ограниченный schedule-черновик с HITL на утверждение."
        ),
    )
    gate = {
        "gate_id": f"gate_{task_id}_6_needs_approval",
        "kind": "needs_approval",
        "reason": "Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.",
        "questions": [
            {
                "id": "release",
                "text": "Утвердить выпуск прогнозного schedule файла для текущего набора WCONPROD (поля ORAT, BHP)?",
                "expected_format": "approve / reject / reply",
                "required": True,
            },
            {
                "id": "conditions",
                "text": "Если нужны правки — укажите keyword, поле и откуда взять факт.",
                "expected_format": "свободный текст",
                "required": False,
            },
        ],
        "expected_version": 6,
    }
    await _set_gate(task_id, status="awaiting_human", version=6, gate=gate)
    return {
        "task_id": task_id,
        "count": len(stored),
        "ui": f"/t/{task_id}",
        "awaiting_human": True,
        "human_gate": gate,
        "hitl_backend": hitl_backend(),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/knowledge")
def knowledge_page() -> FileResponse:
    return FileResponse(STATIC / "knowledge.html")


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
            "ingest_hint": "После сохранения поднимите Knowledge Ingestion (n8n), чтобы PG/RAG подхватил новую revision.",
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
        "ingest_hint": "После сохранения поднимите Knowledge Ingestion (n8n), чтобы PG/RAG подхватил новую revision.",
    }


@app.post("/v1/knowledge/documents")
def knowledge_create_document(
    body: KnowledgeDocumentCreate,
    _: None = Depends(_require_key),
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
        "ingest_hint": "Создано в JSON corpus. Запустите MAS — Knowledge Ingestion, чтобы runtime RAG увидел карточку.",
    }


@app.patch("/v1/knowledge/documents/{target_base}/{knowledge_id}")
def knowledge_patch_document(
    target_base: str,
    knowledge_id: str,
    body: KnowledgeDocumentPatch,
    _: None = Depends(_require_key),
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
        "ingest_hint": "Сохранено в JSON corpus. Запустите MAS — Knowledge Ingestion, чтобы runtime RAG увидел revision "
        + str(doc.get("revision")),
    }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
