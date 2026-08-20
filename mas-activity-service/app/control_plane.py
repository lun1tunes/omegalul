"""Case / event / error store: Postgres when DATABASE_URL is set, otherwise memory."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.contracts import empty_state

_LOCK = threading.Lock()
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
        "when_to_use": "Исходный SCHEDULE (.inc): сдвиг дат ввода по фактам Excel; перепривязка скважин в группу по тексту задачи и baseline. Excel нужен только для новых дат ввода.",
        "input_required": ["schedule_source"],
        "output_provides": ["schedule_out", "diff"],
    },
]
_EVENT_SEQ = 0
_ERROR_SEQ = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_memory() -> None:
    global _EVENT_SEQ, _ERROR_SEQ
    with _LOCK:
        _CASES.clear()
        _EVENTS.clear()
        _ERRORS.clear()
        _EXECUTIONS.clear()
        _EVENT_SEQ = 0
        _ERROR_SEQ = 0


def _dsn() -> str:
    from app.settings import get_settings

    return (get_settings().database_url or "").strip()


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_dsn(), row_factory=dict_row)


_SCHEMA_FILE = Path(__file__).resolve().parent / "sql" / "control_plane.sql"

VERIFY_CONTROL_PLANE_SQL = """
SELECT
  to_regclass('public.cases') IS NOT NULL AS cases_ok,
  to_regclass('public.events') IS NOT NULL AS events_ok,
  to_regclass('public.error_traces') IS NOT NULL AS error_traces_ok,
  to_regclass('public.executions') IS NOT NULL AS executions_ok,
  to_regclass('public.agent_registry') IS NOT NULL AS agent_registry_ok,
  (SELECT COUNT(*) FROM agent_registry) AS agent_count
"""


def control_plane_sql() -> str:
    return _SCHEMA_FILE.read_text(encoding="utf-8")


def sql_statements(sql_text: str) -> list[str]:
    """Split on ';' outside of SQL string literals. Full-line `--` comments are dropped first."""
    cleaned = "\n".join(
        line for line in sql_text.splitlines() if not line.strip().startswith("--")
    )
    stmts: list[str] = []
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
            buf.append(ch)
            i += 1
            continue
        if ch == "'" and not in_string:
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and not in_string:
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts



def ensure_schema() -> dict[str, Any]:
    """CREATE IF NOT EXISTS for cases/events/error_traces/executions/agent_registry.

    No-op without DATABASE_URL (unit tests use memory). Idempotent on a live n8n DB.
    """
    if not _dsn():
        return {"ok": True, "backend": "memory"}
    with _connect() as conn:
        for stmt in sql_statements(control_plane_sql()):
            conn.execute(stmt)
        conn.commit()
        row = conn.execute(VERIFY_CONTROL_PLANE_SQL).fetchone() or {}
    return {"ok": True, "backend": "postgres", **dict(row)}


def _json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.loads(json.dumps(value, default=str))


def create_case(
    case_id: str,
    goal: str,
    artifacts: dict[str, Any] | None = None,
    task_name: str = "",
) -> dict[str, Any]:
    state = empty_state(case_id, goal)
    name = str(task_name or "").strip()
    if name:
        state["task_name"] = name
    if artifacts:
        state["artifacts"] = artifacts
    if _dsn():
        with _connect() as conn:
            conn.execute(
                "INSERT INTO cases (case_id, state, status, updated_at) VALUES (%s, %s::jsonb, %s, now())",
                (case_id, json.dumps(state), "new"),
            )
            conn.commit()
        return get_case(case_id) or {"case_id": case_id, "state": state, "status": "new"}
    with _LOCK:
        _CASES[case_id] = {"case_id": case_id, "state": state, "status": "new", "updated_at": _now()}
        _EVENTS.setdefault(case_id, [])
        _ERRORS.setdefault(case_id, [])
        return dict(_CASES[case_id])


def get_case(case_id: str) -> dict[str, Any] | None:
    if _dsn():
        with _connect() as conn:
            row = conn.execute(
                "SELECT case_id, state, status, updated_at FROM cases WHERE case_id = %s",
                (case_id,),
            ).fetchone()
        return dict(row) if row else None
    with _LOCK:
        row = _CASES.get(case_id)
        return dict(row) if row else None


def list_cases(limit: int = 80) -> list[dict[str, Any]]:
    if _dsn():
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT c.case_id, c.status, c.updated_at, c.state,
                       (SELECT COUNT(*) FROM events e WHERE e.case_id = c.case_id) AS event_count
                FROM cases c
                ORDER BY c.updated_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    with _LOCK:
        items = sorted(_CASES.values(), key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        out = []
        for row in items[:limit]:
            item = dict(row)
            item["event_count"] = len(_EVENTS.get(row["case_id"], []))
            out.append(item)
        return out


def update_case(case_id: str, *, state: dict[str, Any] | None = None, status: str | None = None) -> dict[str, Any]:
    current = get_case(case_id)
    if current is None:
        raise KeyError(case_id)
    next_state = state if state is not None else current["state"]
    next_status = status if status is not None else current["status"]
    if isinstance(next_state, dict):
        next_state = {**next_state, "status": next_status, "case_id": case_id}
    if _dsn():
        with _connect() as conn:
            conn.execute(
                "UPDATE cases SET state = %s::jsonb, status = %s, updated_at = now() WHERE case_id = %s",
                (json.dumps(next_state), next_status, case_id),
            )
            conn.commit()
        return get_case(case_id) or current
    with _LOCK:
        _CASES[case_id] = {
            "case_id": case_id,
            "state": next_state,
            "status": next_status,
            "updated_at": _now(),
        }
        return dict(_CASES[case_id])


_EVENT_SELECT = (
    "event_id, case_id, task_id, kind, actor, agent_id, status, "
    "status_message, handoff_message, payload, created_at"
)


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def event_write_key(
    *,
    kind: Any,
    actor: Any = "",
    agent_id: Any = None,
    status: Any = None,
    status_message: Any = None,
    handoff_message: Any = None,
    task_id: Any = None,
) -> tuple[str, ...]:
    """Identity of one chat line. Same key → do not insert again."""
    return (
        str(kind or ""),
        str(actor or ""),
        str(agent_id or ""),
        str(status or ""),
        _norm_text(status_message),
        _norm_text(handoff_message),
        str(task_id or ""),
    )


def _row_write_key(row: dict[str, Any]) -> tuple[str, ...]:
    return event_write_key(
        kind=row.get("kind"),
        actor=row.get("actor"),
        agent_id=row.get("agent_id"),
        status=row.get("status"),
        status_message=row.get("status_message"),
        handoff_message=row.get("handoff_message"),
        task_id=row.get("task_id"),
    )


def append_event(
    case_id: str,
    *,
    kind: str,
    actor: str,
    agent_id: str | None = None,
    status: str | None = None,
    status_message: str | None = None,
    handoff_message: str | None = None,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    incoming = event_write_key(
        kind=kind,
        actor=actor,
        agent_id=agent_id,
        status=status,
        status_message=status_message,
        handoff_message=handoff_message,
        task_id=task_id,
    )
    if _dsn():
        with _connect() as conn:
            conn.execute("SELECT case_id FROM cases WHERE case_id = %s FOR UPDATE", (case_id,))
            last = conn.execute(
                f"SELECT {_EVENT_SELECT} FROM events WHERE case_id = %s ORDER BY event_id DESC LIMIT 1",
                (case_id,),
            ).fetchone()
            if last and _row_write_key(dict(last)) == incoming:
                conn.commit()
                return {**dict(last), "idempotent": True}
            row = conn.execute(
                f"""
                INSERT INTO events (case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING {_EVENT_SELECT}
                """,
                (case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, json.dumps(payload)),
            ).fetchone()
            conn.commit()
        return dict(row)
    global _EVENT_SEQ
    with _LOCK:
        existing = _EVENTS.setdefault(case_id, [])
        if existing and _row_write_key(existing[-1]) == incoming:
            return {**dict(existing[-1]), "idempotent": True}
        _EVENT_SEQ += 1
        event = {
            "event_id": _EVENT_SEQ,
            "case_id": case_id,
            "task_id": task_id,
            "kind": kind,
            "actor": actor,
            "agent_id": agent_id,
            "status": status,
            "status_message": status_message,
            "handoff_message": handoff_message,
            "payload": payload,
            "created_at": _now(),
        }
        existing.append(event)
        if case_id in _CASES:
            _CASES[case_id]["updated_at"] = event["created_at"]
        return dict(event)


def list_events(case_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    if _dsn():
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload, created_at
                FROM events
                WHERE case_id = %s AND event_id > %s
                ORDER BY event_id ASC
                """,
                (case_id, after_seq),
            ).fetchall()
        return [dict(row) for row in rows]
    with _LOCK:
        return [dict(event) for event in _EVENTS.get(case_id, []) if int(event["event_id"]) > after_seq]


def append_error_trace(
    *,
    case_id: str | None,
    execution_id: str | None,
    workflow_name: str | None,
    node_name: str | None,
    error_message: str | None,
    error_type: str | None,
    stack: str | None,
    input_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _dsn():
        with _connect() as conn:
            row = conn.execute(
                """
                INSERT INTO error_traces (case_id, execution_id, workflow_name, node_name, error_message, error_type, stack, input_snapshot)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING error_id, case_id, execution_id, workflow_name, node_name, error_message, error_type, stack, input_snapshot, created_at
                """,
                (case_id, execution_id, workflow_name, node_name, error_message, error_type, stack, json.dumps(input_snapshot or {})),
            ).fetchone()
            conn.commit()
        return dict(row)
    global _ERROR_SEQ
    with _LOCK:
        _ERROR_SEQ += 1
        row = {
            "error_id": _ERROR_SEQ,
            "case_id": case_id,
            "execution_id": execution_id,
            "workflow_name": workflow_name,
            "node_name": node_name,
            "error_message": error_message,
            "error_type": error_type,
            "stack": stack,
            "input_snapshot": input_snapshot or {},
            "created_at": _now(),
        }
        if case_id:
            _ERRORS.setdefault(case_id, []).append(row)
        return dict(row)


def list_errors(case_id: str) -> list[dict[str, Any]]:
    if _dsn():
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT error_id, case_id, execution_id, workflow_name, node_name, error_message, error_type, stack, input_snapshot, created_at
                FROM error_traces
                WHERE case_id = %s
                ORDER BY error_id ASC
                """,
                (case_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    with _LOCK:
        return [dict(row) for row in _ERRORS.get(case_id, [])]


def record_execution(execution_id: str, case_id: str, workflow_name: str = "orchestrator") -> None:
    if _dsn():
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO executions (execution_id, case_id, workflow_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (execution_id) DO UPDATE SET case_id = EXCLUDED.case_id, workflow_name = EXCLUDED.workflow_name
                """,
                (execution_id, case_id, workflow_name),
            )
            conn.commit()
        return
    with _LOCK:
        _EXECUTIONS[execution_id] = {"execution_id": execution_id, "case_id": case_id, "workflow_name": workflow_name}


def case_id_for_execution(execution_id: str) -> str | None:
    if _dsn():
        with _connect() as conn:
            row = conn.execute(
                "SELECT case_id FROM executions WHERE execution_id = %s",
                (execution_id,),
            ).fetchone()
        return str(row["case_id"]) if row and row.get("case_id") else None
    with _LOCK:
        row = _EXECUTIONS.get(execution_id)
        return str(row["case_id"]) if row and row.get("case_id") else None


def list_agents() -> list[dict[str, Any]]:
    if _dsn():
        try:
            with _connect() as conn:
                rows = conn.execute(
                    "SELECT agent_id, title, when_to_use, input_required, output_provides FROM agent_registry ORDER BY agent_id"
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return list(_REGISTRY)
    return list(_REGISTRY)


def upsert_agent(row: dict[str, Any]) -> None:
    if _dsn():
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_registry (agent_id, title, when_to_use, input_required, output_provides)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (agent_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    when_to_use = EXCLUDED.when_to_use,
                    input_required = EXCLUDED.input_required,
                    output_provides = EXCLUDED.output_provides
                """,
                (
                    row["agent_id"],
                    row.get("title"),
                    row.get("when_to_use"),
                    json.dumps(row.get("input_required") or []),
                    json.dumps(row.get("output_provides") or []),
                ),
            )
            conn.commit()
        return
    with _LOCK:
        existing = [item for item in _REGISTRY if item["agent_id"] != row["agent_id"]]
        existing.append(row)
        _REGISTRY[:] = existing
