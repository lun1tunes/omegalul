"""Control-plane facade.

Production storage is reached only through the n8n control-plane webhook.
The in-memory backend exists solely for standalone tests and local UI work.
This module intentionally contains no database driver or SQL execution.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.contracts import empty_state
from app.settings import get_settings

_LOCK = threading.Lock()
_CLIENT_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_HTTP: httpx.Client | None = None
_HTTP_KEY: tuple[Any, ...] | None = None
_SNAP_TTL_S = 0.4
_LIST_TTL_S = 1.0
_read_gen = 0
_snap_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_list_cache: tuple[float, int, list[dict[str, Any]]] | None = None
_WRITE_OPS = frozenset({
    "wipe",
    "create_case",
    "update_case",
    "append_event",
    "append_error",
    "record_execution",
    "upsert_agent",
    "artifact_put",
})
_CASES: dict[str, dict[str, Any]] = {}
_EVENTS: dict[str, list[dict[str, Any]]] = {}
_ERRORS: dict[str, list[dict[str, Any]]] = {}
_EXECUTIONS: dict[str, dict[str, Any]] = {}
_REGISTRY: list[dict[str, Any]] = [
    {
        "agent_id": "excel_extractor",
        "title": "Excel Extractor",
        "when_to_use": "Если есть Excel-файл и нужно извлечь скважины, даты, дебиты, управления",
        "input_required": ["excel"],
        "output_provides": ["excel_table", "normalized_rows"],
    },
    {
        "agent_id": "calculation_agent",
        "title": "Calculation Agent",
        "when_to_use": "Если есть структурная поверхность и траектория, нужно найти пересечение и начало интервала перфорации",
        "input_required": ["surface", "trajectory"],
        "output_provides": ["top_perforation_md"],
    },
    {
        "agent_id": "schedule_builder",
        "title": "Schedule Builder",
        "when_to_use": "Исходный SCHEDULE (.inc): сдвиг дат ввода по фактам Excel",
        "input_required": ["schedule_source"],
        "output_provides": ["schedule_out", "diff"],
    },
]
_EVENT_SEQ = 0
_ERROR_SEQ = 0

_SCHEMA_FILE = Path(__file__).resolve().parent / "sql" / "control_plane.sql"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _proxy_url() -> str:
    return get_settings().control_plane_proxy_url.strip()


def _configured() -> bool:
    return bool(_proxy_url())


def configured() -> bool:
    return _configured()


def _headers() -> dict[str, str]:
    cfg = get_settings()
    headers = {"Content-Type": "application/json"}
    if cfg.control_plane_proxy_auth_header and cfg.control_plane_proxy_auth_value:
        headers[cfg.control_plane_proxy_auth_header] = cfg.control_plane_proxy_auth_value
    return headers


def _http_client() -> httpx.Client:
    """Reuse one client: each proxy call used to open a fresh TCP session to n8n."""
    global _HTTP, _HTTP_KEY
    cfg = get_settings()
    key = (_proxy_url(), cfg.control_plane_proxy_timeout_s, cfg.httpx_verify)
    with _CLIENT_LOCK:
        if _HTTP is None or _HTTP_KEY != key:
            if _HTTP is not None:
                _HTTP.close()
            _HTTP = httpx.Client(timeout=key[1], verify=key[2])
            _HTTP_KEY = key
        return _HTTP


def invalidate_read_cache() -> None:
    global _list_cache, _read_gen
    with _CACHE_LOCK:
        _snap_cache.clear()
        _list_cache = None
        _read_gen += 1


def _notify_case(case_id: str | None) -> None:
    cid = str(case_id or "").strip()
    if not cid:
        return
    try:
        from app import case_watch
    except Exception:
        return
    case_watch.notify_case(cid)


def _after_write(operation: str, payload: dict[str, Any]) -> None:
    if operation not in _WRITE_OPS and operation != "batch":
        return
    invalidate_read_cache()
    if operation == "batch":
        seen: set[str] = set()
        for item in payload.get("calls") or []:
            if isinstance(item, dict):
                cid = str(item.get("case_id") or "").strip()
                if cid and cid not in seen:
                    seen.add(cid)
                    _notify_case(cid)
        return
    _notify_case(payload.get("case_id"))


def proxy_call(operation: str, **payload: Any) -> Any:
    """Call the single n8n control-plane webhook and unwrap its result."""
    url = _proxy_url()
    if not url:
        raise RuntimeError("CONTROL_PLANE_PROXY_URL is not configured")
    body = {"operation": operation, **payload}
    try:
        response = _http_client().post(url, json=body, headers=_headers())
    except httpx.HTTPError as exc:
        raise RuntimeError(f"control-plane proxy request failed: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(
            f"control-plane proxy HTTP {response.status_code}: {response.text[:500]}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("control-plane proxy returned invalid JSON") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError(str((data or {}).get("error") if isinstance(data, dict) else data))
    result = data.get("result")
    _after_write(operation, payload)
    return result


def proxy_call_many(calls: list[dict[str, Any]]) -> list[Any]:
    """One n8n execution for several single-row ops; falls back to sequential calls."""
    cleaned = [dict(item) for item in calls if isinstance(item, dict) and item.get("operation")]
    if not cleaned:
        return []
    if len(cleaned) == 1:
        body = dict(cleaned[0])
        operation = str(body.pop("operation"))
        return [proxy_call(operation, **body)]
    try:
        result = proxy_call("batch", calls=cleaned)
    except RuntimeError as exc:
        if "unsupported operation" not in str(exc).lower():
            raise
        out: list[Any] = []
        for item in cleaned:
            body = dict(item)
            operation = str(body.pop("operation"))
            out.append(proxy_call(operation, **body))
        return out
    if isinstance(result, list):
        return result
    return [result]


def reset_memory() -> None:
    global _EVENT_SEQ, _ERROR_SEQ
    with _LOCK:
        _CASES.clear()
        _EVENTS.clear()
        _ERRORS.clear()
        _EXECUTIONS.clear()
        _EVENT_SEQ = 0
        _ERROR_SEQ = 0
    invalidate_read_cache()


def control_plane_sql() -> str:
    return _SCHEMA_FILE.read_text(encoding="utf-8")


def sql_statements(sql_text: str) -> list[str]:
    """Keep the old parser as a safety test for the checked-in schema contract."""
    cleaned = "\n".join(line for line in sql_text.splitlines() if not line.strip().startswith("--"))
    statements: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "'" and in_string:
            if i + 1 < len(cleaned) and cleaned[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_string = False
        elif ch == "'":
            in_string = True
        if ch == ";" and not in_string:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def ensure_schema() -> dict[str, Any]:
    if _configured():
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                return dict(proxy_call("schema") or {})
            except Exception as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(2**attempt)
        raise RuntimeError("control-plane schema initialization failed") from last_error
    return {"ok": True, "backend": "memory"}


def wipe_data() -> dict[str, Any]:
    """Truncate MAS case tables via the n8n proxy. Never called on Activity boot."""
    if _configured():
        return dict(proxy_call("wipe") or {})
    reset_memory()
    return {"ok": True, "backend": "memory", "wiped": True}


def _local_case(case_id: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _CASES.get(case_id)
        return dict(row) if row else None


def create_case(
    case_id: str,
    goal: str,
    artifacts: dict[str, Any] | None = None,
    task_name: str = "",
    extra_state: dict[str, Any] | None = None,
    status: str = "new",
    initial_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = empty_state(case_id, goal)
    if task_name:
        state["task_name"] = str(task_name).strip()
    if artifacts:
        state["artifacts"] = artifacts
    if extra_state:
        state.update(extra_state)
    status = str(status or "new").strip() or "new"
    if _configured():
        calls: list[dict[str, Any]] = [
            {"operation": "create_case", "case_id": case_id, "state": state, "status": status}
        ]
        if initial_event:
            calls.append({"operation": "append_event", "case_id": case_id, **initial_event})
        results = proxy_call_many(calls)
        row = results[0] if results else {}
        return dict(row) if isinstance(row, dict) else {"case_id": case_id, "state": state, "status": status}
    with _LOCK:
        row = {"case_id": case_id, "state": state, "status": status, "updated_at": _now()}
        _CASES[case_id] = row
        _EVENTS.setdefault(case_id, [])
        _ERRORS.setdefault(case_id, [])
    if initial_event:
        append_event(case_id, **initial_event)
        return get_case(case_id) or dict(row)
    return dict(row)


def get_case(case_id: str) -> dict[str, Any] | None:
    if _configured():
        result = proxy_call("get_case", case_id=case_id)
        return dict(result) if isinstance(result, dict) and result.get("case_id") else None
    return _local_case(case_id)


def _copy_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    case = value.get("case")
    events = value.get("events")
    return {
        "case": dict(case) if isinstance(case, dict) else case,
        "events": [dict(item) if isinstance(item, dict) else item for item in (events or [])],
    }


def _store_snapshot(cache_key: tuple[str, int], packed: dict[str, Any], gen: int) -> None:
    with _CACHE_LOCK:
        if gen == _read_gen:
            _snap_cache[cache_key] = (time.monotonic(), packed)


def snapshot(case_id: str, after_seq: int = 0) -> dict[str, Any]:
    """One webhook: case row + events after ``after_seq``.

    Falls back to get_case + list_events if the imported proxy is older
    and does not yet know ``snapshot``. Short TTL coalesces overlapping
    SSE / GET /cases/{id} reads without delaying writes (cache drops on write).
    An in-flight fetch started before a write is not stored after invalidate.
    """
    after_seq = int(after_seq or 0)
    cache_key = (str(case_id), after_seq)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _snap_cache.get(cache_key)
        if hit and now - hit[0] < _SNAP_TTL_S:
            return _copy_snapshot(hit[1])
        gen = _read_gen
    if _configured():
        try:
            result = proxy_call("snapshot", case_id=case_id, after_seq=after_seq) or {}
        except RuntimeError as exc:
            if "unsupported operation" not in str(exc).lower():
                raise
            row = get_case(case_id)
            events = list_events(case_id, after_seq=after_seq) if row else []
            packed = {"case": row, "events": events}
            _store_snapshot(cache_key, packed, gen)
            return _copy_snapshot(packed)
        case = result.get("case") if isinstance(result, dict) else None
        events = result.get("events") if isinstance(result, dict) else None
        row = dict(case) if isinstance(case, dict) and case.get("case_id") else None
        rows = list(events) if isinstance(events, list) else []
        packed = {"case": row, "events": rows}
        _store_snapshot(cache_key, packed, gen)
        return _copy_snapshot(packed)
    row = _local_case(case_id)
    with _LOCK:
        events = [
            dict(item)
            for item in _EVENTS.get(case_id, [])
            if int(item["event_id"]) > after_seq
        ]
    return {"case": row, "events": events}


def list_cases(limit: int = 80) -> list[dict[str, Any]]:
    global _list_cache
    limit = int(limit or 80)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _list_cache
        if hit and hit[1] == limit and now - hit[0] < _LIST_TTL_S:
            return [dict(row) for row in hit[2]]
        gen = _read_gen
    if _configured():
        result = list(proxy_call("list_cases", limit=limit) or [])
        rows = [
            dict(row)
            for row in result
            if isinstance(row, dict) and str(row.get("case_id") or "").strip()
        ]
        with _CACHE_LOCK:
            if gen == _read_gen:
                _list_cache = (time.monotonic(), limit, rows)
        return [dict(row) for row in rows]
    with _LOCK:
        rows = sorted(_CASES.values(), key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        out: list[dict[str, Any]] = []
        for row in rows[:limit]:
            events = [dict(item) for item in _EVENTS.get(row["case_id"], [])]
            out.append({**row, "event_count": len(events), "events": events})
        return out


def update_case(case_id: str, *, state: dict[str, Any] | None = None, status: str | None = None) -> dict[str, Any]:
    if state is None or status is None:
        current = get_case(case_id)
        if current is None:
            raise KeyError(case_id)
        next_state = state if state is not None else current["state"]
        next_status = status if status is not None else current["status"]
    else:
        next_state = state
        next_status = status
    if isinstance(next_state, dict):
        next_state = {**next_state, "status": next_status, "case_id": case_id}
    if _configured():
        return dict(proxy_call("update_case", case_id=case_id, state=next_state, status=next_status) or {})
    with _LOCK:
        row = {"case_id": case_id, "state": next_state, "status": next_status, "updated_at": _now()}
        _CASES[case_id] = row
        return dict(row)


def update_case_and_append(
    case_id: str,
    *,
    state: dict[str, Any],
    status: str,
    kind: str,
    actor: str,
    agent_id: str | None = None,
    event_status: str | None = None,
    status_message: str | None = None,
    handoff_message: str | None = None,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One proxy batch: persist case row then append an event."""
    next_state = {**state, "status": status, "case_id": case_id} if isinstance(state, dict) else state
    event = {
        "kind": kind,
        "actor": actor,
        "agent_id": agent_id,
        "status": event_status,
        "status_message": status_message,
        "handoff_message": handoff_message,
        "task_id": task_id,
        "payload": payload or {},
    }
    if _configured():
        results = proxy_call_many(
            [
                {"operation": "update_case", "case_id": case_id, "state": next_state, "status": status},
                {"operation": "append_event", "case_id": case_id, **event},
            ]
        )
        return dict(results[1] or {}) if len(results) > 1 and isinstance(results[1], dict) else {}
    update_case(case_id, state=next_state, status=status)
    return append_event(case_id, **event)


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def event_write_key(*, kind: Any, actor: Any = "", agent_id: Any = None, status: Any = None,
                    status_message: Any = None, handoff_message: Any = None, task_id: Any = None) -> tuple[str, ...]:
    return (
        str(kind or ""), str(actor or ""), str(agent_id or ""), str(status or ""),
        _norm_text(status_message), _norm_text(handoff_message), str(task_id or ""),
    )


def _row_write_key(row: dict[str, Any]) -> tuple[str, ...]:
    return event_write_key(
        kind=row.get("kind"), actor=row.get("actor"), agent_id=row.get("agent_id"),
        status=row.get("status"), status_message=row.get("status_message"),
        handoff_message=row.get("handoff_message"), task_id=row.get("task_id"),
    )


def append_event(case_id: str, *, kind: str, actor: str, agent_id: str | None = None,
                 status: str | None = None, status_message: str | None = None,
                 handoff_message: str | None = None, task_id: str | None = None,
                 payload: dict[str, Any] | None = None) -> dict[str, Any]:
    values = {
        "case_id": case_id, "kind": kind, "actor": actor, "agent_id": agent_id,
        "status": status, "status_message": status_message, "handoff_message": handoff_message,
        "task_id": task_id, "payload": payload or {},
    }
    if _configured():
        return dict(proxy_call("append_event", **values) or {})
    global _EVENT_SEQ
    with _LOCK:
        existing = _EVENTS.setdefault(case_id, [])
        incoming = event_write_key(**{k: values[k] for k in ("kind", "actor", "agent_id", "status", "status_message", "handoff_message", "task_id")})
        if existing and _row_write_key(existing[-1]) == incoming:
            return {**dict(existing[-1]), "idempotent": True}
        _EVENT_SEQ += 1
        row = {"event_id": _EVENT_SEQ, **values, "created_at": _now()}
        existing.append(row)
        if case_id in _CASES:
            _CASES[case_id]["updated_at"] = row["created_at"]
        return dict(row)


def list_events(case_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    if _configured():
        return list(proxy_call("list_events", case_id=case_id, after_seq=after_seq) or [])
    with _LOCK:
        return [dict(row) for row in _EVENTS.get(case_id, []) if int(row["event_id"]) > after_seq]


def append_error_trace(*, case_id: str | None, execution_id: str | None, workflow_name: str | None,
                       node_name: str | None, error_message: str | None, error_type: str | None,
                       stack: str | None, input_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    values = {
        "case_id": case_id, "execution_id": execution_id, "workflow_name": workflow_name,
        "node_name": node_name, "error_message": error_message, "error_type": error_type,
        "stack": stack, "input_snapshot": input_snapshot or {},
    }
    if _configured():
        return dict(proxy_call("append_error", **values) or {})
    global _ERROR_SEQ
    with _LOCK:
        _ERROR_SEQ += 1
        row = {"error_id": _ERROR_SEQ, **values, "created_at": _now()}
        if case_id:
            _ERRORS.setdefault(case_id, []).append(row)
        return dict(row)


def list_errors(case_id: str) -> list[dict[str, Any]]:
    if _configured():
        return list(proxy_call("list_errors", case_id=case_id) or [])
    with _LOCK:
        return [dict(row) for row in _ERRORS.get(case_id, [])]


def record_execution(execution_id: str, case_id: str, workflow_name: str = "orchestrator") -> None:
    if _configured():
        proxy_call("record_execution", execution_id=execution_id, case_id=case_id, workflow_name=workflow_name)
        return
    with _LOCK:
        _EXECUTIONS[execution_id] = {"execution_id": execution_id, "case_id": case_id, "workflow_name": workflow_name}


def case_id_for_execution(execution_id: str) -> str | None:
    if _configured():
        result = proxy_call("case_id_for_execution", execution_id=execution_id)
        return str(result) if result else None
    with _LOCK:
        row = _EXECUTIONS.get(execution_id)
        return str(row["case_id"]) if row and row.get("case_id") else None


def list_agents() -> list[dict[str, Any]]:
    if _configured():
        return list(proxy_call("list_agents") or [])
    with _LOCK:
        return [dict(row) for row in _REGISTRY]


def upsert_agent(row: dict[str, Any]) -> None:
    if _configured():
        proxy_call("upsert_agent", row=row)
        return
    with _LOCK:
        _REGISTRY[:] = [item for item in _REGISTRY if item["agent_id"] != row["agent_id"]]
        _REGISTRY.append(dict(row))
