"""Excel Extractor agent: AgentTask → tools → AgentResult. No SCHEDULE writes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .sessions import init_state, load_state, locked_session, new_session_id, save_state, session_dir, session_file
from .tools import execute_tool
from . import excel_tools as _excel_tools  # noqa: F401  # register detect_tables / query_table
from . import state_tools as _state_tools  # noqa: F401

logger = logging.getLogger(__name__)
ACTIVITY = os.getenv("ACTIVITY_BASE_URL", "").rstrip("/")
WELL_COL = ("скважина", "well", "wellname", "well_name", "object", "name")
DATE_COL = (
    "дата ввода",
    "датаввода",
    "дат. ввод",
    "date",
    "commissioning",
    "commissioning_date",
    "start_date",
    "ввод",
    "дат",
)


def _post_event(case_id: str, payload: dict[str, Any]) -> None:
    base = str(payload.pop("_activity_base", "") or ACTIVITY).rstrip("/")
    if not case_id or not base:
        return
    try:
        req = Request(
            f"{base}/cases/{case_id}/events",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=8).read()
    except Exception:
        logger.exception("failed to emit agent event case_id=%s kind=%s", case_id, payload.get("kind"))


def _emit(
    case_id: str,
    *,
    kind: str,
    task_id: str,
    activity: str,
    status_message: str,
    status: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    body: dict[str, Any] = {
        "kind": kind,
        "actor": "excel_extractor",
        "agent_id": "excel_extractor",
        "task_id": task_id,
        "status_message": status_message,
        "_activity_base": activity,
    }
    if status:
        body["status"] = status
    if payload:
        body["payload"] = payload
    _post_event(case_id, body)


def emit_tool_progress(state: dict[str, Any], tool_name: str, tool_result: dict[str, Any]) -> None:
    """Live Activity lines when the n8n AI Agent calls excel-tools HTTP tools."""
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    case_id = str(payload.get("case_id") or "")
    if not case_id:
        return
    inner = tool_result.get("result") if isinstance(tool_result.get("result"), dict) else {}
    message = ""
    if tool_name == "workbook_introspect":
        sheets = inner.get("sheets") if isinstance(inner.get("sheets"), list) else []
        message = f"Смотрю структуру Excel: листов {len(sheets)}"
    elif tool_name == "detect_tables":
        tables = inner.get("tables") or inner.get("detected_tables") or []
        n = len(tables) if isinstance(tables, list) else 0
        message = f"Нашёл таблиц: {n}"
    elif tool_name == "match_tables":
        message = "Подбираю таблицу под запрос"
    elif tool_name == "describe_table":
        message = "Смотрю колонки таблицы"
    elif tool_name == "list_column_values":
        message = "Читаю значения колонки"
    elif tool_name == "query_table":
        rows = inner.get("preview") or inner.get("rows") or inner.get("records") or []
        n = inner.get("row_count") if isinstance(inner.get("row_count"), int) else (len(rows) if isinstance(rows, list) else 0)
        message = f"Читаю таблицу: {n} строк"
    elif tool_name == "sheet_preview":
        message = "Смотрю фрагмент листа"
    if not message:
        return
    _emit(
        case_id,
        kind="agent.progress",
        task_id=str(payload.get("task_id") or ""),
        activity=str(payload.get("activity_base") or ACTIVITY),
        status_message=message,
    )


def _fetch_bytes(url: str) -> bytes:
    with urlopen(url, timeout=30) as resp:
        return resp.read()


def _artifact_card(artifacts: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        item = artifacts.get(key)
        if isinstance(item, dict) and item:
            return item
    return {}


def _task_activity(task: dict[str, Any]) -> str:
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    return str(inputs.get("activity_base_url") or ACTIVITY).rstrip("/")


def _load_excel(task: dict[str, Any]) -> tuple[str, str]:
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    artifacts = inputs.get("artifacts") if isinstance(inputs.get("artifacts"), dict) else {}
    excel = _artifact_card(artifacts, "excel", "file")
    path = inputs.get("excel_path") or excel.get("path")
    url = inputs.get("excel_url") or inputs.get("artifact_url")
    activity = _task_activity(task)
    case_id = str(task.get("case_id") or "")
    artifact_id = str(excel.get("artifact_id") or "excel")
    if activity and case_id and not url:
        url = f"{activity}/cases/{case_id}/artifacts/{artifact_id}"
    data: bytes | None = None
    filename = str(excel.get("filename") or inputs.get("excel_filename") or "workbook.xlsx")
    if path and Path(path).is_file():
        data = Path(path).read_bytes()
        filename = Path(path).name
    elif url:
        try:
            data = _fetch_bytes(url)
        except Exception as exc:
            raise FileNotFoundError(f"excel artifact fetch failed: {url}: {exc}") from exc
    if not data:
        raise FileNotFoundError("excel artifact is missing")
    session_id = new_session_id()
    session_dir(session_id, create=True)
    suffix = Path(filename).suffix.lower() or ".xlsx"
    relative = f"input{suffix}"
    dest = session_file(session_id, relative)
    dest.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    init_state(
        session_id=session_id,
        file_path=relative,
        file_name=filename,
        file_hash=f"sha256:{digest}",
        file_size=len(data),
        payload={
            "agent_id": "excel_extractor",
            "case_id": case_id,
            "task_id": str(task.get("task_id") or ""),
            "objective": str(task.get("objective") or ""),
            "handoff_message": str(task.get("handoff_message") or ""),
            "activity_base": activity,
        },
    )
    return session_id, filename


def _norm_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("ё", "е")


def _looks_well_col(name: str) -> bool:
    key = _norm_key(name)
    if any(token in key for token in DATE_COL):
        return False
    return any(token in key for token in WELL_COL)


def _looks_date_col(name: str) -> bool:
    key = _norm_key(name)
    return any(token in key for token in DATE_COL)


def _as_well(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _as_date(value: Any) -> Any:
    if value in (None, ""):
        return None
    if hasattr(value, "strftime") and not isinstance(value, str):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return str(value)
    return value


def _query_rows(state: dict[str, Any], session_id: str, payload: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    columns = list(payload.get("columns") or [])
    rows = list(payload.get("preview_rows") or payload.get("preview") or payload.get("rows") or [])
    result_id = payload.get("result_id")
    rel = ""
    if result_id:
        rel = str(((state.get("result_sets") or {}).get(result_id) or {}).get("path") or "")
    if rel:
        path = session_file(session_id, rel)
        if path.is_file():
            loaded: list[dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    loaded.append(item)
            if loaded:
                rows = loaded
    return columns, rows


def _agent_result(
    task_id: str,
    status: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    requests: list[Any] | None = None,
    assumptions: list[Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "agent_id": "excel_extractor",
        "status": status,
        "message": message,
        "data": data or {},
        "artifacts": artifacts or {},
        "issues": issues or [],
        "assumptions": assumptions or [],
        "requests": requests or [],
    }


def _store_result(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    state["agent_result"] = result
    save_state(str(state["session_id"]), state)
    return result


def suggested_capability(objective: str, handoff: str = "") -> str:
    text = f"{objective} {handoff}".casefold()
    if any(token in text for token in ("дат", "ввод", "commission", "скважин", "well", "дебит")):
        return "commissioning"
    if not text.strip():
        return "commissioning"
    return "operations"


def _compact_inspect(state: dict[str, Any]) -> dict[str, Any]:
    intro = execute_tool(state, "workbook_introspect", {})
    meta = intro.get("result") if isinstance(intro, dict) else {}
    detect = execute_tool(state, "detect_tables", {"max_tables": 10})
    raw = detect.get("result") if isinstance(detect, dict) else {}
    tables = raw.get("tables") if isinstance(raw, dict) else []
    if not isinstance(tables, list):
        tables = []
    compact: list[dict[str, Any]] = []
    for table in tables[:12]:
        if not isinstance(table, dict):
            continue
        cols = table.get("columns") if isinstance(table.get("columns"), list) else []
        compact.append(
            {
                "table_id": table.get("table_id"),
                "sheet": table.get("sheet"),
                "range": table.get("range"),
                "row_count": table.get("row_count"),
                "columns": cols[:24],
                "columns_truncated": len(cols) > 24,
                "title": table.get("title") or "",
            }
        )
    sheets = meta.get("sheets") if isinstance(meta, dict) else []
    return {
        "file_name": (meta.get("file_name") if isinstance(meta, dict) else "") or state.get("file_name"),
        "sheets": sheets[:40] if isinstance(sheets, list) else [],
        "table_count": len(tables),
        "tables": compact,
    }


def open_session(task: dict[str, Any]) -> dict[str, Any]:
    case_id = str(task.get("case_id") or "")
    task_id = str(task.get("task_id") or "")
    activity = _task_activity(task)
    _emit(
        case_id,
        kind="agent.accepted",
        task_id=task_id,
        activity=activity,
        status_message="Разбираю Excel",
    )
    try:
        session_id, filename = _load_excel(task)
    except FileNotFoundError as exc:
        result = _agent_result(
            task_id,
            "needs_input",
            "Нет Excel-файла для извлечения",
            issues=[{"type": "missing_excel", "detail": str(exc)}],
            requests=[{"question_id": "Q-excel", "question": "Приложите workbook .xlsx", "options": []}],
        )
        _emit(
            case_id,
            kind="agent.result",
            task_id=task_id,
            activity=activity,
            status="needs_input",
            status_message=result["message"],
        )
        return {"ok": False, "status": "needs_input", "task_id": task_id, "result": result}
    except Exception as exc:
        result = _agent_result(
            task_id,
            "failed",
            str(exc)[:400],
            issues=[{"type": "excel_load_failed", "detail": str(exc)[:400]}],
        )
        _emit(
            case_id,
            kind="agent.failed",
            task_id=task_id,
            activity=activity,
            status="failed",
            status_message=str(exc)[:400],
        )
        return {"ok": False, "status": "failed", "task_id": task_id, "result": result}

    with locked_session(session_id):
        state = load_state(session_id)
        inspect = _compact_inspect(state)
    n_sheets = len(inspect.get("sheets") or [])
    n_tables = int(inspect.get("table_count") or 0)
    _emit(
        case_id,
        kind="agent.progress",
        task_id=task_id,
        activity=activity,
        status_message=f"Файл {filename}: листов {n_sheets}, таблиц {n_tables}",
    )
    capability = suggested_capability(str(task.get("objective") or ""), str(task.get("handoff_message") or ""))
    return {
        "ok": True,
        "session_id": session_id,
        "task_id": task_id,
        "file_name": filename,
        "inspect": inspect,
        "objective": str(task.get("objective") or ""),
        "handoff_message": str(task.get("handoff_message") or ""),
        "suggested_capability": capability,
    }


def extract_commissioning(session_id: str) -> dict[str, Any]:
    with locked_session(session_id):
        state = load_state(session_id)
        payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
        case_id = str(payload.get("case_id") or "")
        task_id = str(payload.get("task_id") or "")
        activity = str(payload.get("activity_base") or ACTIVITY)
        filename = str(state.get("file_name") or "workbook.xlsx")
        detect = execute_tool(state, "detect_tables", {})
        tables = []
        raw = detect.get("result") if isinstance(detect, dict) else {}
        if isinstance(raw, dict):
            tables = raw.get("tables") or raw.get("detected_tables") or []
        if not isinstance(tables, list):
            tables = []
        _emit(
            case_id,
            kind="agent.progress",
            task_id=task_id,
            activity=activity,
            status_message=f"Нашёл таблиц: {len(tables)}. Читаю скважины и даты",
        )
        rows_out: list[dict[str, Any]] = []
        table_ids: list[str] = []
        for table in tables[:8]:
            if not isinstance(table, dict):
                continue
            table_id = str(table.get("table_id") or table.get("id") or "")
            if not table_id:
                continue
            table_ids.append(table_id)
            queried = execute_tool(state, "query_table", {"table_id": table_id, "limit": 10000})
            tool_payload = queried.get("result") if isinstance(queried, dict) else queried
            preview: list[dict[str, Any]] = []
            columns: list[str] = []
            if isinstance(tool_payload, dict):
                columns, preview = _query_rows(state, session_id, tool_payload)
            rows_out.append(
                {
                    "table_id": table_id,
                    "columns": columns,
                    "preview": preview[:200],
                    "row_count": len(preview),
                    "records": preview,
                }
            )
    facts = commissioning_facts(
        [
            {"columns": item.get("columns") or [], "preview": item.get("records") or item.get("preview") or []}
            for item in rows_out
        ]
    )
    for item in rows_out:
        item.pop("records", None)
    _emit(
        case_id,
        kind="agent.progress",
        task_id=task_id,
        activity=activity,
        status_message=f"Фактов скважина+дата: {len(facts)}",
    )
    result = _agent_result(
        task_id,
        "completed",
        (
            f"Извлечено таблиц: {len(table_ids)} из {filename}"
            + (f", фактов скважина+дата: {len(facts)}" if facts else "")
        ),
        data={
            "excel_table": table_ids[0] if table_ids else None,
            "table_ids": table_ids,
            "normalized_rows": rows_out,
            "facts": facts,
            "session_id": session_id,
            "file_name": filename,
        },
        artifacts={"excel_session": session_id},
    )
    with locked_session(session_id):
        state = load_state(session_id)
        _store_result(state, result)
    _emit(
        case_id,
        kind="agent.result",
        task_id=task_id,
        activity=activity,
        status="completed",
        status_message=result["message"],
        payload={"tables": len(table_ids), "facts": len(facts)},
    )
    return result


def session_result(session_id: str) -> dict[str, Any]:
    state = load_state(session_id)
    result = state.get("agent_result")
    if isinstance(result, dict) and result.get("status"):
        return result
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    return _agent_result(
        str(payload.get("task_id") or ""),
        "failed",
        "Агент не вызвал extract_commissioning — факты Excel не собраны",
        issues=[{"type": "no_extract"}],
    )


def commissioning_facts(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for table in tables:
        preview = table.get("preview") if isinstance(table, dict) else []
        if not isinstance(preview, list):
            continue
        for row in preview:
            if not isinstance(row, dict):
                continue
            well = ""
            date: Any = None
            for key, value in row.items():
                if _looks_well_col(str(key)) and not well:
                    well = _as_well(value)
                elif _looks_date_col(str(key)) and date in (None, ""):
                    date = _as_date(value)
            if not well or date in (None, ""):
                continue
            token = f"{well}|{date}"
            if token in seen:
                continue
            seen.add(token)
            facts.append({"well": well, "date": date, "values": row})
    return facts


def run_excel_agent(task: dict[str, Any]) -> dict[str, Any]:
    opened = open_session(task)
    if not opened.get("ok"):
        result = opened.get("result")
        if isinstance(result, dict):
            return result
        return _agent_result(str(task.get("task_id") or ""), "failed", "Excel session failed")
    return extract_commissioning(str(opened["session_id"]))
