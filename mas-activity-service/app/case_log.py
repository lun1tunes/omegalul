"""Developer log of a case («Лог» tab, ``GET /cases/{id}/log``).

The chat shows the engineer what happened; the log shows the developer *how*: every event of the
case (including ``trace.*`` rows the chat hides), levelled, attributed to a source, grouped by
orchestrator step and linked to the n8n execution that produced it. One case = one trace.

Record shape (one per event; ``system.node_error`` rows are merged with ``error_traces``)::

    {seq, at, level, source, kind, step, task_id, agent_id, title, message,
     execution_id, execution_url, duration_ms, detail}

``level`` is derived deterministically from kind + payload (no free-form levels from producers,
except ``trace.note`` which may name its own). ``source`` is ``engineer`` / ``orchestrator`` /
``agent:<id>`` / ``n8n``. ``title`` is a short technical line — identifiers are allowed here, this
view is for developers, not for the engineer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app import control_plane
from app.contracts import is_trace_kind

LEVELS = ("debug", "info", "warn", "error")
_LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
ORCHESTRATOR = "orchestrator"


# -- one record -----------------------------------------------------------------------------------


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def parse_ts(value: Any) -> datetime | None:
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


def record_level(kind: str, event: dict[str, Any]) -> str:
    payload = _payload(event)
    status = str(event.get("status") or "")
    if kind in {"agent.failed", "system.node_error", "case.failed"}:
        return "error"
    if kind == "agent.result":
        return "error" if status == "failed" else "info"
    if kind == "trace.tool":
        return "warn" if payload.get("ok") is False else "debug"
    if kind == "trace.note":
        wanted = str(payload.get("level") or "").strip().lower()
        return wanted if wanted in _LEVEL_RANK else "debug"
    if kind in {"agent.progress", "orchestrator.status"}:
        return "debug"
    if kind == "orchestrator.decision" and payload.get("guard"):
        # A deterministic guard overrode / corrected the LLM decision — worth a developer's eye.
        return "warn"
    return "info"


def record_source(kind: str, event: dict[str, Any]) -> str:
    actor = str(event.get("actor") or "").strip()
    agent_id = str(event.get("agent_id") or "").strip()
    if kind in {"case.created", "hitl.answered", "case.cancelled"}:
        return "engineer"
    if kind == "system.node_error":
        return "n8n"
    if kind.startswith("orchestrator.") or kind in {"agent.handoff", "case.finished", "case.failed", "hitl.request"}:
        return ORCHESTRATOR
    if agent_id:
        return f"agent:{agent_id}"
    if actor and actor not in {ORCHESTRATOR, "n8n", "user", "human", "engineer"}:
        return f"agent:{actor}"
    return ORCHESTRATOR


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def actor_of(event: dict[str, Any]) -> str:
    return str(event.get("actor") or "").strip()


def record_title(kind: str, event: dict[str, Any]) -> str:
    payload = _payload(event)
    agent = str(event.get("agent_id") or event.get("actor") or "agent")
    task_id = str(event.get("task_id") or "")
    status = str(event.get("status") or "")
    if kind == "case.created":
        return "Задача создана"
    if kind == "orchestrator.decision":
        action = str(payload.get("action_type") or "?")
        title = f"Решение: {action}"
        if payload.get("agent_id"):
            title += f" → {payload['agent_id']}"
        if payload.get("guard"):
            title += f" · guard {payload['guard']}"
        return title
    if kind == "orchestrator.status":
        return f"Оркестратор: {payload.get('action_type') or 'статус'}"
    if kind == "orchestrator.resume":
        src = str(payload.get("source") or actor_of(event) or "system")
        return f"Возобновление: источник {src}" + (f" ({payload.get('task_id')})" if payload.get("task_id") else "")
    if kind == "agent.handoff":
        return f"Передача {task_id or 'задачи'} → {agent}"
    if kind == "agent.accepted":
        return f"{agent}: принял задачу"
    if kind == "agent.progress":
        return f"{agent}: прогресс" + (f" ({status})" if status and status != "running" else "")
    if kind == "agent.result":
        return f"{agent}: результат {status or 'completed'}"
    if kind == "agent.failed":
        return f"{agent}: ошибка"
    if kind == "hitl.request":
        qid = str(payload.get("question_id") or "")
        return "Вопрос инженеру" + (f" {qid}" if qid else "")
    if kind == "hitl.answered":
        return "Ответ инженера"
    if kind == "case.finished":
        return "Задача завершена"
    if kind == "case.failed":
        return "Задача завершилась с ошибкой"
    if kind == "case.cancelled":
        return "Задача закрыта"
    if kind == "system.node_error":
        node = str(payload.get("node_name") or "?")
        wf = str(payload.get("workflow_name") or "")
        return f"n8n: упал узел {node}" + (f" ({wf})" if wf else "")
    if kind == "trace.tool":
        tool = str(payload.get("tool") or "tool")
        outcome = "ok" if payload.get("ok") is not False else f"ошибка {payload.get('error') or ''}".strip()
        return f"{agent}: {tool} → {outcome}"
    if kind == "trace.note":
        return f"{agent}: {_short(payload.get('title') or event.get('status_message') or 'заметка', 120)}"
    return kind


def execution_url(payload: dict[str, Any], n8n_base: str) -> str | None:
    """Link to the n8n execution: the Error Trigger gives one; orchestrator/agent events give ids."""
    explicit = str(payload.get("execution_url") or "").strip()
    if explicit:
        return explicit
    execution_id = str(payload.get("execution_id") or "").strip()
    workflow_id = str(payload.get("workflow_id") or "").strip()
    base = str(n8n_base or "").rstrip("/")
    if execution_id and workflow_id and base:
        return f"{base}/workflow/{workflow_id}/executions/{execution_id}"
    return None


def log_record(event: dict[str, Any], *, step: int | None = None, n8n_base: str = "") -> dict[str, Any]:
    kind = str(event.get("kind") or "")
    payload = _payload(event)
    detail: dict[str, Any] = dict(payload)
    if event.get("handoff_message"):
        detail["handoff_message"] = event.get("handoff_message")
    duration = payload.get("duration_ms")
    return {
        "seq": event.get("event_id"),
        "at": _iso(event.get("created_at")),
        "level": record_level(kind, event),
        "source": record_source(kind, event),
        "kind": kind,
        "step": step,
        "task_id": event.get("task_id") or None,
        "agent_id": event.get("agent_id") or None,
        "title": record_title(kind, event),
        "message": " ".join(str(event.get("status_message") or "").split()),
        "execution_id": str(payload.get("execution_id") or "") or None,
        "execution_url": execution_url(payload, n8n_base),
        "duration_ms": int(duration) if isinstance(duration, (int, float)) else None,
        "detail": detail,
    }


# -- the whole log --------------------------------------------------------------------------------


def _error_record(row: dict[str, Any], *, step: int | None, n8n_base: str) -> dict[str, Any]:
    """An ``error_traces`` row without a matching ``system.node_error`` event (older error workflow)."""
    payload = {
        "error_id": row.get("error_id"),
        "execution_id": row.get("execution_id"),
        "workflow_name": row.get("workflow_name"),
        "node_name": row.get("node_name"),
        "error_type": row.get("error_type"),
        "error_message": row.get("error_message"),
        "stack": row.get("stack"),
    }
    event = {
        "event_id": None,
        "created_at": row.get("created_at"),
        "kind": "system.node_error",
        "actor": "n8n",
        "status": "error",
        "status_message": f"Упал узел {row.get('node_name') or 'unknown'}",
        "payload": payload,
    }
    record = log_record(event, step=step, n8n_base=n8n_base)
    record["seq"] = f"error-{row.get('error_id')}"
    return record


def _attach_error_traces(records: list[dict[str, Any]], errors: Iterable[dict[str, Any]], *, n8n_base: str) -> list[dict[str, Any]]:
    by_error_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["kind"] != "system.node_error":
            continue
        error_id = record["detail"].get("error_id")
        if error_id is not None:
            by_error_id[str(error_id)] = record
    extra: list[dict[str, Any]] = []
    for row in errors:
        if not isinstance(row, dict):
            continue
        record = by_error_id.get(str(row.get("error_id")))
        if record is None:
            # Attribute to the step that was running when the node failed.
            before = [r for r in records if r["at"] and row.get("created_at") and str(r["at"]) <= str(_iso(row.get("created_at")))]
            step = before[-1]["step"] if before else None
            extra.append(_error_record(row, step=step, n8n_base=n8n_base))
            continue
        record["detail"] = {
            **record["detail"],
            "error_message": row.get("error_message"),
            "error_type": row.get("error_type"),
            "stack": row.get("stack"),
            "workflow_name": record["detail"].get("workflow_name") or row.get("workflow_name"),
            "node_name": record["detail"].get("node_name") or row.get("node_name"),
        }
        if not record["execution_url"]:
            record["execution_url"] = execution_url(record["detail"], n8n_base)
        record["title"] = record_title("system.node_error", {"payload": record["detail"]})
    if extra:
        records = sorted(records + extra, key=lambda r: (str(r["at"] or ""), str(r["seq"] or "")))
    return records


def records_from_events(events: list[dict[str, Any]], *, n8n_base: str = "") -> list[dict[str, Any]]:
    """Level + source + step for every raw event, in event order. Step 0 = before the first decision."""
    records: list[dict[str, Any]] = []
    step = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        payload = _payload(event)
        if kind == "orchestrator.decision":
            try:
                step = int(payload.get("step_count") or step + 1)
            except (TypeError, ValueError):
                step += 1
        records.append(log_record(event, step=step, n8n_base=n8n_base))
    return records


def _duration_ms(start: Any, end: Any) -> int | None:
    a, b = parse_ts(start), parse_ts(end)
    if a is None or b is None:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def step_groups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per orchestrator step: decision, agent, time span, worst level, counters."""
    groups: dict[int, dict[str, Any]] = {}
    for record in records:
        step = record.get("step")
        key = int(step) if isinstance(step, int) else 0
        group = groups.setdefault(
            key,
            {"step": key, "title": "Старт" if key == 0 else f"Шаг {key}", "decision": None, "agent_id": None,
             "action_type": None, "started_at": record["at"], "ended_at": record["at"], "level": "debug",
             "records": 0, "tool_calls": 0, "errors": 0},
        )
        group["records"] += 1
        group["ended_at"] = record["at"] or group["ended_at"]
        if record["kind"] == "orchestrator.decision":
            group["decision"] = record["title"]
            group["action_type"] = record["detail"].get("action_type")
            group["agent_id"] = record["detail"].get("agent_id") or group["agent_id"]
        if record["kind"] == "trace.tool":
            group["tool_calls"] += 1
        if record["level"] == "error":
            group["errors"] += 1
        if _LEVEL_RANK[record["level"]] > _LEVEL_RANK[group["level"]]:
            group["level"] = record["level"]
    out = []
    for key in sorted(groups):
        group = groups[key]
        group["duration_ms"] = _duration_ms(group["started_at"], group["ended_at"])
        out.append(group)
    return out


def summarize(records: list[dict[str, Any]], *, status: str | None) -> dict[str, Any]:
    agents = sorted({r["agent_id"] for r in records if r.get("agent_id")})
    started = records[0]["at"] if records else None
    ended = records[-1]["at"] if records else None
    return {
        "status": status,
        "records": len(records),
        "steps": max((int(r["step"]) for r in records if isinstance(r.get("step"), int)), default=0),
        "tool_calls": sum(1 for r in records if r["kind"] == "trace.tool"),
        "hitl_rounds": sum(1 for r in records if r["kind"] == "hitl.request"),
        "handoffs": sum(1 for r in records if r["kind"] == "agent.handoff"),
        "errors": sum(1 for r in records if r["level"] == "error"),
        "warnings": sum(1 for r in records if r["level"] == "warn"),
        "agents": agents,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": _duration_ms(started, ended),
    }


def build_case_log(case_id: str, *, n8n_base: str = "") -> dict[str, Any] | None:
    snap = control_plane.snapshot(case_id)
    row = snap.get("case")
    if row is None:
        return None
    records = records_from_events(list(snap.get("events") or []), n8n_base=n8n_base)
    records = _attach_error_traces(records, control_plane.list_errors(case_id), n8n_base=n8n_base)
    status = row.get("status")
    return {
        "case_id": case_id,
        "status": status,
        "summary": summarize(records, status=status),
        "steps": step_groups(records),
        "records": records,
    }


def to_ndjson(log: dict[str, Any]) -> str:
    """One JSON object per line: header, then every record — the file to attach to a bug report."""
    header = {"case_id": log.get("case_id"), "status": log.get("status"), "summary": log.get("summary"), "steps": log.get("steps")}
    lines = [json.dumps({"type": "case", **header}, ensure_ascii=False, default=str)]
    lines.extend(json.dumps({"type": "record", **record}, ensure_ascii=False, default=str) for record in log.get("records") or [])
    return "\n".join(lines) + "\n"


__all__ = [
    "LEVELS",
    "build_case_log",
    "is_trace_kind",
    "log_record",
    "record_level",
    "record_source",
    "record_title",
    "records_from_events",
    "step_groups",
    "summarize",
    "to_ndjson",
]
