"""In-memory MAS activity feed for chat-style handoff presentation + HITL."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.enrich import enrich_turn
from app.orchestrator import OrchestratorError, hitl_backend, invoke_orchestrator
from app import knowledge as knowledge_store

STATIC = Path(__file__).resolve().parents[1] / "static"
ACTIVITY_KEY = os.getenv("MAS_ACTIVITY_KEY", "dev-local")
MAX_TURNS_PER_TASK = 500
MAX_TASKS = 200
MAX_BODY_BYTES = 256_000
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
HITL_ACTIONS = frozenset({"status", "reply", "approve", "reject", "cancel"})
ANON = frozenset({"", "anonymous", "anon", "n/a", "na", "unknown"})

app = FastAPI(
    title="MAS Activity Service",
    version="0.3.0",
    description="Live handoff transcript + HITL composer for Petroleum Engineering MAS.",
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
    turn_count: int
    last_status: str | None = None
    last_at_abs: str | None = None
    status: str | None = None
    awaiting_human: bool = False


_lock = asyncio.Lock()
_tasks: dict[str, dict[str, Any]] = {}
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_order: list[str] = []
_started_at = datetime.now(timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_store() -> None:
    """Test helper — clears in-memory state."""
    _tasks.clear()
    _subscribers.clear()
    _order.clear()


def _require_key(x_activity_key: str | None = Header(default=None, alias="X-Activity-Key")) -> None:
    if not secrets.compare_digest(str(x_activity_key or ""), ACTIVITY_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Activity-Key")


def _touch(task_id: str) -> dict[str, Any]:
    if task_id not in _tasks:
        _tasks[task_id] = {
            "task_id": task_id,
            "title": None,
            "updated_at": _now(),
            "turns": [],
            "status": None,
            "version": None,
            "gate": None,
        }
        _order.append(task_id)
        while len(_order) > MAX_TASKS:
            old = _order.pop(0)
            _tasks.pop(old, None)
            _subscribers.pop(old, None)
    return _tasks[task_id]


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


def _awaiting(task: dict[str, Any]) -> bool:
    return (task.get("status") == "awaiting_human") and bool(_gate_payload(task))


async def _publish(task_id: str, message: dict[str, Any]) -> None:
    queues = list(_subscribers.get(task_id, []))
    for queue in queues:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


async def _append_turns(task_id: str, turns: list[dict[str, Any]], *, title: str | None = None) -> list[dict[str, Any]]:
    async with _lock:
        task = _touch(task_id)
        if title:
            task["title"] = title
        stored = []
        for turn in turns:
            turn = dict(turn)
            turn["turn_id"] = len(task["turns"]) + 1
            task["turns"].append(turn)
            stored.append(turn)
        if len(task["turns"]) > MAX_TURNS_PER_TASK:
            task["turns"] = task["turns"][-MAX_TURNS_PER_TASK :]
            for index, item in enumerate(task["turns"], start=1):
                item["turn_id"] = index
        task["updated_at"] = _now()
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
            task["status"] = status
        if version is not None:
            task["version"] = version
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
        if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
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
        "version": "0.3.0",
        "hitl_backend": hitl_backend(),
        "tasks": len(_tasks),
        "time": _now(),
        "started_at": _started_at,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    if not STATIC.exists() or not (STATIC / "index.html").exists():
        raise HTTPException(status_code=503, detail="UI assets missing")
    return {"status": "ready", "hitl_backend": hitl_backend()}


@app.post("/v1/turns")
async def post_turn(body: TurnPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    turn = _normalize_turn(body.turn, trace_id=body.trace_id)
    stored = await _append_turns(body.task_id, [turn])
    return {"stored": True, "task_id": body.task_id, "turn": stored[0]}


@app.post("/v1/sync")
async def post_sync(body: SyncPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    turns = [_normalize_turn(t, trace_id=body.trace_id) for t in body.turns]
    turns.extend(_events_to_turns(body.events, trace_id=body.trace_id))
    gate = body.human_gate
    status = body.status
    for event in body.events:
        if event.human_gate and not gate:
            gate = event.human_gate
        if event.status and not status:
            status = event.status
    if gate or status is not None or body.version is not None:
        await _set_gate(
            body.task_id,
            status=status,
            version=body.version,
            gate=gate,
            clear_gate=gate is None and status is not None and status != "awaiting_human",
        )
    if not turns:
        return {"stored": False, "task_id": body.task_id, "count": 0, "reason": "no_handoff_events"}
    stored = await _append_turns(body.task_id, turns)
    return {"stored": True, "task_id": body.task_id, "count": len(stored), "turns": stored}


@app.get("/v1/tasks")
def list_tasks(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    rows = []
    for task_id in reversed(_order[-limit:]):
        task = _tasks.get(task_id)
        if not task:
            continue
        last = task["turns"][-1] if task["turns"] else {}
        rows.append(
            TaskMeta(
                task_id=task_id,
                title=task.get("title"),
                updated_at=task["updated_at"],
                turn_count=len(task["turns"]),
                last_status=last.get("status"),
                last_at_abs=last.get("at_abs"),
                status=task.get("status"),
                awaiting_human=_awaiting(task),
            ).model_dump()
        )
    return {"tasks": rows}


def _task_feed(task_id: str) -> dict[str, Any]:
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "contract": "mas_activity_feed",
        "contract_version": "1.1",
        "task_id": task_id,
        "title": task.get("title"),
        "updated_at": task["updated_at"],
        "status": task.get("status"),
        "version": task.get("version"),
        "awaiting_human": _awaiting(task),
        "human_gate": _gate_payload(task),
        "hitl_backend": hitl_backend(),
        "activity": task["turns"],
    }


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    return _task_feed(task_id)


@app.get("/v1/tasks/{task_id}/gate")
async def get_gate(task_id: str, refresh: bool = Query(default=False)) -> dict[str, Any]:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    task = _tasks.get(task_id)
    if not task and not refresh:
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
            awaiting = status_payload.get("status") == "awaiting_human" and gate and gate.get("gate_id")
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

    clear = orch.get("status") != "awaiting_human" or not orch.get("human_gate")
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
        "awaiting_human": orch.get("status") == "awaiting_human" and bool(gate_out),
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
                    "activity": task["turns"],
                    "updated_at": task["updated_at"],
                    "status": task.get("status"),
                    "version": task.get("version"),
                    "awaiting_human": _awaiting(task),
                    "human_gate": _gate_payload(task),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'snapshot', 'task_id': task_id, 'activity': [], 'awaiting_human': False, 'human_gate': None})}\n\n"
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
    stored = await _append_turns(task_id, [_normalize_turn(t) for t in demo], title="Demo presentation run")
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
