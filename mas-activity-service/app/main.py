"""In-memory MAS activity feed for chat-style handoff presentation."""

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
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app.enrich import enrich_turn

STATIC = Path(__file__).resolve().parents[1] / "static"
ACTIVITY_KEY = os.getenv("MAS_ACTIVITY_KEY", "dev-local")
MAX_TURNS_PER_TASK = 500
MAX_TASKS = 200
MAX_BODY_BYTES = 256_000
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")

app = FastAPI(
    title="MAS Activity Service",
    version="0.2.0",
    description="Live handoff transcript for Petroleum Engineering MAS presentation UI.",
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


class SyncPost(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    trace_id: str | None = None
    events: list[EventIn] = Field(default_factory=list)
    turns: list[TurnIn] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_RE.match(value):
            raise ValueError("task_id has invalid characters")
        return value


class TaskMeta(BaseModel):
    task_id: str
    title: str | None = None
    updated_at: str
    turn_count: int
    last_status: str | None = None
    last_at_abs: str | None = None


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


async def _limit_body(request: Request) -> None:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")


def _touch(task_id: str) -> dict[str, Any]:
    if task_id not in _tasks:
        _tasks[task_id] = {"task_id": task_id, "title": None, "updated_at": _now(), "turns": []}
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
        queues = list(_subscribers.get(task_id, []))
    for queue in queues:
        for turn in stored:
            try:
                queue.put_nowait(turn)
            except asyncio.QueueFull:
                pass
    return stored


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
        "version": "0.2.0",
        "tasks": len(_tasks),
        "time": _now(),
        "started_at": _started_at,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    if not STATIC.exists() or not (STATIC / "index.html").exists():
        raise HTTPException(status_code=503, detail="UI assets missing")
    return {"status": "ready"}


@app.post("/v1/turns")
async def post_turn(body: TurnPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    turn = _normalize_turn(body.turn, trace_id=body.trace_id)
    stored = await _append_turns(body.task_id, [turn])
    return {"stored": True, "task_id": body.task_id, "turn": stored[0]}


@app.post("/v1/sync")
async def post_sync(body: SyncPost, _: None = Depends(_require_key)) -> dict[str, Any]:
    turns = [_normalize_turn(t, trace_id=body.trace_id) for t in body.turns]
    turns.extend(_events_to_turns(body.events, trace_id=body.trace_id))
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
            ).model_dump()
        )
    return {"tasks": rows}


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "contract": "mas_activity_feed",
        "contract_version": "1.1",
        "task_id": task_id,
        "title": task.get("title"),
        "updated_at": task["updated_at"],
        "activity": task["turns"],
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
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'snapshot', 'task_id': task_id, 'activity': []})}\n\n"
            while True:
                try:
                    turn = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps({'type': 'turn', 'task_id': task_id, 'turn': turn}, ensure_ascii=False)}\n\n"
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
            summary="Orchestrator delegates workbook extraction to Excel Extractor.",
            brief=(
                "Оркестратор выбрал Excel Extractor для извлечения ORAT из workbook. "
                "Специалист получит только bounded packet: цель, лимиты и нужные поля."
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
            summary="Excel returned 12 fact(s) for Schedule Builder.",
            brief=(
                "Excel Extractor завершил extract и собрал 12 фактов со snapshot. "
                "Пакет готов для Schedule Builder; workbook в оркестратор не передаётся."
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
            summary="Resuming Schedule Builder with 13 fact(s).",
            brief=(
                "Excel вернул недостающий BHP с тем же correlation_id. "
                "Schedule Builder продолжит на новой версии evidence packet."
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
            summary="SCHEDULE draft ready for accountable human release.",
            brief=(
                "Schedule Builder прошёл bounded gates и подготовил draft .inc. "
                "Релиз остаётся за человеком; specialist сам себя не утверждает."
            ),
            from_role="Schedule Builder",
            to_role="Orchestrator",
            from_specialist="schedule_builder_specialist",
            to_specialist="universal_orchestrator",
            details={"release_ready": True},
            duration_ms=22100,
        ),
    ]
    stored = await _append_turns(task_id, [_normalize_turn(t) for t in demo], title="Demo presentation run")
    return {"task_id": task_id, "count": len(stored), "ui": f"/t/{task_id}"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/t/{task_id}")
def task_page(task_id: str) -> FileResponse:
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(status_code=400, detail="Invalid task_id")
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
