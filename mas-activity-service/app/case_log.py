"""Developer log of a case («Лог» tab, ``GET /cases/{id}/log``).

The chat shows the engineer what happened; the log shows the developer *how*: every event of the
case (including ``trace.*`` rows the chat hides), levelled, attributed to a source, grouped by
orchestrator step and linked to the n8n execution that produced it. One case = one trace.

Record shape (one per event; ``system.node_error`` rows are merged with ``error_traces``)::

    {seq, at, level, source, kind, step, task_id, agent_id, actor, status, title, message,
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
    if kind == "trace.rag":
        rag_status = str(payload.get("status") or "").strip().lower()
        return "warn" if rag_status in {"unavailable", "failed", "empty", "abstain", "needs_input"} else "debug"
    if kind == "trace.llm":
        finish = str(payload.get("finish_reason") or "").strip().lower()
        if payload.get("error") or finish == "length":
            return "warn"
        return "debug"
    if kind == "orchestrator.status" and payload.get("reason") == "orchestrator_timeout":
        return "warn"
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
        llm = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}
        parse = payload.get("llm_parse") if isinstance(payload.get("llm_parse"), dict) else {}
        parse_err = str(parse.get("output_parser") or parse.get("error") or "").strip()
        if parse_err:
            title += f" · {parse_err[:80]}"
        finish = str(llm.get("finish_reason") or parse.get("finish_reason") or "").strip()
        if finish:
            title += f" · {finish}"
        prompt = llm.get("prompt_tokens")
        completion = llm.get("completion_tokens")
        if prompt not in (None, "") or completion not in (None, ""):
            title += f" · {int(prompt or 0) + int(completion or 0)} tok"
        else:
            tokens = parse.get("completion_tokens")
            if tokens not in (None, "", 0):
                title += f" · {tokens} tok"
        think = llm.get("reasoning_tokens") if llm.get("reasoning_tokens") not in (None, "", 0) else parse.get("reasoning_tokens")
        if think not in (None, "", 0):
            title += f" · think {think}"
        return title
    if kind == "orchestrator.status":
        if payload.get("reason") == "orchestrator_timeout":
            elapsed = payload.get("elapsed_s")
            limit = payload.get("timeout_s")
            extra = ""
            if elapsed is not None and limit is not None:
                extra = f" {elapsed}s / {limit}s"
            return f"Оркестратор: timeout{extra}"
        return f"Оркестратор: {payload.get('action_type') or payload.get('reason') or 'статус'}"
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
    if kind == "trace.rag":
        rag_status = str(payload.get("status") or "?")
        cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
        title = f"База знаний: {rag_status} · {len(cards)} карточек"
        phase = str(payload.get("phase") or "").strip()
        if phase:
            title += f" · {phase}"
        caller = str(payload.get("caller") or "").strip()
        if caller:
            title += f" · {caller}"
        codes: list[str] = []
        for finding in (payload.get("findings") if isinstance(payload.get("findings"), list) else [])[:4]:
            if isinstance(finding, dict) and finding.get("code"):
                codes.append(str(finding["code"]))
            elif isinstance(finding, str) and finding.strip():
                codes.append(finding.strip())
        if codes:
            title += f" · {', '.join(codes)}"
        return title
    if kind == "trace.llm":
        role = str(payload.get("role") or "llm")
        finish = str(payload.get("finish_reason") or "stop")
        tokens = int(payload.get("prompt_tokens") or 0) + int(payload.get("completion_tokens") or 0)
        title = f"{role}: {finish} · {tokens} tok"
        think = payload.get("reasoning_tokens")
        if think not in (None, "", 0):
            title += f" · think {think}"
        return title
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
        "actor": event.get("actor") or None,
        "status": event.get("status") or None,
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


def _stamp_gaps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``gap_ms`` from the previous record's ``at`` (None on the first row)."""
    prev_at = None
    for record in records:
        record["gap_ms"] = _duration_ms(prev_at, record.get("at"))
        prev_at = record.get("at")
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
        sc = payload.get("step_count")
        if kind == "orchestrator.decision":
            try:
                step = int(sc if sc is not None else step + 1)
            except (TypeError, ValueError):
                step += 1
        elif sc is not None and kind in {"trace.rag", "trace.llm"}:
            try:
                step = int(sc)
            except (TypeError, ValueError):
                pass
        records.append(log_record(event, step=step, n8n_base=n8n_base))
    return _stamp_gaps(records)


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


def tool_calls_by_agent(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record.get("kind") != "trace.tool":
            continue
        agent_id = str(record.get("agent_id") or "").strip()
        if not agent_id:
            continue
        counts[agent_id] = counts.get(agent_id, 0) + 1
    return dict(sorted(counts.items()))


_RAG_EMPTY_STATUSES = frozenset({"unavailable", "empty", "abstain"})
_LLM_TRUNCATED_ERRORS = frozenset({"llm_truncated_empty", "llm_truncated"})


def _record_detail(record: dict[str, Any]) -> dict[str, Any]:
    detail = record.get("detail")
    if isinstance(detail, dict):
        return detail
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _is_llm_truncated(record: dict[str, Any]) -> bool:
    """``finish_reason=length`` or parser ``llm_truncated_empty`` — thinking ate the answer."""
    kind = str(record.get("kind") or "")
    detail = _record_detail(record)
    if kind == "trace.llm":
        finish = str(detail.get("finish_reason") or "").strip().lower()
        err = str(detail.get("error") or "").strip().lower()
        return finish == "length" or err in _LLM_TRUNCATED_ERRORS
    if kind == "orchestrator.decision":
        parse = detail.get("llm_parse") if isinstance(detail.get("llm_parse"), dict) else {}
        llm = detail.get("llm") if isinstance(detail.get("llm"), dict) else {}
        err = str(parse.get("error") or parse.get("output_parser") or "").strip().lower()
        finish = str(llm.get("finish_reason") or parse.get("finish_reason") or "").strip().lower()
        return finish == "length" or err in _LLM_TRUNCATED_ERRORS
    return False


def _is_rag_empty(record: dict[str, Any]) -> bool:
    if str(record.get("kind") or "") != "trace.rag":
        return False
    detail = _record_detail(record)
    status = str(detail.get("status") or "").strip().lower()
    if status == "failed":
        return False
    cards = detail.get("cards") if isinstance(detail.get("cards"), list) else []
    if status in _RAG_EMPTY_STATUSES:
        return True
    return not cards


def _rag_actor(record: dict[str, Any]) -> str:
    agent = str(record.get("agent_id") or "").strip()
    if agent:
        return agent
    detail = _record_detail(record)
    caller = str(detail.get("caller") or "").strip()
    if caller:
        return caller
    source = str(record.get("source") or "")
    if source.startswith("agent:"):
        return source.split(":", 1)[-1] or ORCHESTRATOR
    return ORCHESTRATOR


def knowledge_counts(records: list[dict[str, Any]]) -> dict[str, Any]:
    """T5 counters for ``summarize`` / live ``report.json``.

    ``kb_calls`` = agent ``trace.rag`` with ``phase=on_demand`` (``retrieve_knowledge``).
    The starting Attach is ``phase=initial`` and must not green the phase-8 floor.
    """
    empty_by: dict[str, int] = {}
    kb_by: dict[str, int] = {}
    truncated = 0
    empty = 0
    for record in records:
        if _is_llm_truncated(record):
            truncated += 1
        kind = str(record.get("kind") or "")
        if kind != "trace.rag":
            continue
        actor = _rag_actor(record)
        if actor != ORCHESTRATOR:
            phase = str(_record_detail(record).get("phase") or "").strip().lower()
            if phase == "on_demand":
                kb_by[actor] = kb_by.get(actor, 0) + 1
        if _is_rag_empty(record):
            empty += 1
            empty_by[actor] = empty_by.get(actor, 0) + 1
    return {
        "llm_truncated": truncated,
        "rag_empty": empty,
        "rag_empty_by_agent": dict(sorted(empty_by.items())),
        "kb_calls": sum(kb_by.values()),
        "kb_calls_by_agent": dict(sorted(kb_by.items())),
    }


def _quality_warning(record: dict[str, Any]) -> bool:
    """α2 warnings: LLM/tool quality, not Activity waiting for a slow webhook ack."""
    if record.get("level") != "warn":
        return False
    detail = _record_detail(record)
    if record.get("kind") == "orchestrator.status" and detail.get("reason") == "orchestrator_timeout":
        return False
    # Truncation is ``llm_truncated`` (7.5), not the generic warning bucket.
    if record.get("kind") == "trace.llm" and _is_llm_truncated(record):
        return False
    # Empty / unavailable RAG is expected until phase 8 (Builder commissioning has no keyword scope).
    # A failed retrieval is a real outage and still counts.
    if record.get("kind") == "trace.rag":
        return str(detail.get("status") or "").strip().lower() == "failed"
    return True


def summarize(records: list[dict[str, Any]], *, status: str | None) -> dict[str, Any]:
    agents = sorted({r["agent_id"] for r in records if r.get("agent_id")})
    started = records[0]["at"] if records else None
    ended = records[-1]["at"] if records else None
    by_agent = tool_calls_by_agent(records)
    knowledge = knowledge_counts(records)
    return {
        "status": status,
        "records": len(records),
        "steps": max((int(r["step"]) for r in records if isinstance(r.get("step"), int)), default=0),
        "tool_calls": sum(1 for r in records if r["kind"] == "trace.tool"),
        "tool_calls_by_agent": by_agent,
        "hitl_rounds": sum(1 for r in records if r["kind"] == "hitl.request"),
        "handoffs": sum(1 for r in records if r["kind"] == "agent.handoff"),
        "errors": sum(1 for r in records if r["level"] == "error"),
        "warnings": sum(1 for r in records if _quality_warning(r)),
        "llm_truncated": knowledge["llm_truncated"],
        "rag_empty": knowledge["rag_empty"],
        "rag_empty_by_agent": knowledge["rag_empty_by_agent"],
        "kb_calls": knowledge["kb_calls"],
        "kb_calls_by_agent": knowledge["kb_calls_by_agent"],
        "agents": agents,
        "started_at": started,
        "ended_at": ended,
        "duration_ms": _duration_ms(started, ended),
    }


def _pointer(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    seq = record.get("seq")
    title = record.get("title") or record.get("kind") or ""
    return f" seq={seq} {title}".rstrip()


def threshold_violations(
    records: list[dict[str, Any]],
    *,
    max_warnings: int | None = 0,
    max_steps: int | None = 6,
    max_tool_calls_per_agent: int | None = 4,
    max_llm_truncated: int | None = 0,
    max_rag_empty: int | None = None,
    min_kb_calls: int | None = None,
) -> list[str]:
    """α2 + T5: exceeding a live threshold names the first log record over the limit.

    ``None`` skips that check (recovery cases that are *expected* to warn or retry).
    Guard decisions and failed ``trace.tool`` both count as ``warnings`` — same as ``summarize``.
    Commissioning/datasets live sets ``max_rag_empty=0`` after 8.1.
    ``min_kb_calls`` is on for ``excel_apply_dataset`` after 8.4 (Builder ``retrieve_knowledge``).
    """
    problems: list[str] = []
    warns = [r for r in records if _quality_warning(r)]
    if max_warnings is not None and len(warns) > max_warnings:
        extra = warns[max_warnings] if len(warns) > max_warnings else warns[-1]
        problems.append(f"warnings={len(warns)} > {max_warnings}:{_pointer(extra)}")
    steps = max((int(r["step"]) for r in records if isinstance(r.get("step"), int)), default=0)
    if max_steps is not None and steps > max_steps:
        extra = next((r for r in reversed(records) if r.get("step") == steps), None)
        problems.append(f"steps={steps} > {max_steps}:{_pointer(extra)}")
    if max_tool_calls_per_agent is not None:
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            if record.get("kind") != "trace.tool":
                continue
            agent_id = str(record.get("agent_id") or "").strip()
            if not agent_id:
                continue
            by_agent.setdefault(agent_id, []).append(record)
        for agent_id, calls in sorted(by_agent.items()):
            n = len(calls)
            if n > max_tool_calls_per_agent:
                extra = calls[max_tool_calls_per_agent]
                problems.append(f"tool_calls[{agent_id}]={n} > {max_tool_calls_per_agent}:{_pointer(extra)}")
    truncated = [r for r in records if _is_llm_truncated(r)]
    if max_llm_truncated is not None and len(truncated) > max_llm_truncated:
        extra = truncated[max_llm_truncated] if len(truncated) > max_llm_truncated else truncated[-1]
        problems.append(f"llm_truncated={len(truncated)} > {max_llm_truncated}:{_pointer(extra)}")
    empty = [r for r in records if _is_rag_empty(r)]
    if max_rag_empty is not None and len(empty) > max_rag_empty:
        extra = empty[max_rag_empty] if len(empty) > max_rag_empty else empty[-1]
        problems.append(f"rag_empty={len(empty)} > {max_rag_empty}:{_pointer(extra)}")
    kb = knowledge_counts(records)["kb_calls"]
    if min_kb_calls is not None and kb < min_kb_calls:
        problems.append(f"kb_calls={kb} < {min_kb_calls}")
    return problems


def metrics_for_cases(*, since: datetime | None = None, n8n_base: str = "") -> dict[str, Any]:
    """Compact per-case ``summarize`` for ``GET /metrics/cases`` (no records, no page)."""
    cases_out: list[dict[str, Any]] = []
    for row in control_plane.list_cases():
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            continue
        if since is not None:
            updated = parse_ts(row.get("updated_at") or row.get("created_at"))
            if updated is not None and updated < since:
                continue
        log = build_case_log(case_id, n8n_base=n8n_base)
        if log is None:
            continue
        summary = log.get("summary") or {}
        started = parse_ts(summary.get("started_at"))
        if since is not None and (started is None or started < since):
            continue
        cases_out.append({"case_id": case_id, "status": log.get("status"), "summary": summary})
    totals = {
        "count": len(cases_out),
        "steps": sum(int((c["summary"] or {}).get("steps") or 0) for c in cases_out),
        "tool_calls": sum(int((c["summary"] or {}).get("tool_calls") or 0) for c in cases_out),
        "warnings": sum(int((c["summary"] or {}).get("warnings") or 0) for c in cases_out),
        "errors": sum(int((c["summary"] or {}).get("errors") or 0) for c in cases_out),
        "hitl_rounds": sum(int((c["summary"] or {}).get("hitl_rounds") or 0) for c in cases_out),
        "llm_truncated": sum(int((c["summary"] or {}).get("llm_truncated") or 0) for c in cases_out),
        "rag_empty": sum(int((c["summary"] or {}).get("rag_empty") or 0) for c in cases_out),
        "kb_calls": sum(int((c["summary"] or {}).get("kb_calls") or 0) for c in cases_out),
        "duration_ms": sum(
            int(c["summary"]["duration_ms"])
            for c in cases_out
            if isinstance((c.get("summary") or {}).get("duration_ms"), int)
        ),
    }
    return {"since": _iso(since) if since else None, "cases": cases_out, "totals": totals}


def build_case_log(case_id: str, *, n8n_base: str = "") -> dict[str, Any] | None:
    snap = control_plane.snapshot(case_id)
    row = snap.get("case")
    if row is None:
        return None
    records = records_from_events(list(snap.get("events") or []), n8n_base=n8n_base)
    records = _stamp_gaps(_attach_error_traces(records, control_plane.list_errors(case_id), n8n_base=n8n_base))
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
    "metrics_for_cases",
    "parse_ts",
    "record_level",
    "record_source",
    "record_title",
    "records_from_events",
    "step_groups",
    "knowledge_counts",
    "summarize",
    "threshold_violations",
    "to_ndjson",
    "tool_calls_by_agent",
]
