"""Excel Extractor agent: AgentTask → tools → AgentResult. No SCHEDULE writes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .sessions import init_state, load_state, locked_session, new_session_id, session_dir, session_file
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


def _fetch_bytes(url: str) -> bytes:
    with urlopen(url, timeout=30) as resp:
        return resp.read()


def _load_excel(task: dict[str, Any]) -> tuple[str, str]:
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    artifacts = inputs.get("artifacts") if isinstance(inputs.get("artifacts"), dict) else {}
    excel = artifacts.get("excel") if isinstance(artifacts.get("excel"), dict) else {}
    path = inputs.get("excel_path") or excel.get("path")
    url = inputs.get("excel_url") or inputs.get("artifact_url")
    activity = str(inputs.get("activity_base_url") or ACTIVITY).rstrip("/")
    case_id = str(task.get("case_id") or "")
    artifact_id = excel.get("artifact_id") or "excel"
    if activity and case_id and not url:
        url = f"{activity}/cases/{case_id}/artifacts/{artifact_id}"
    data: bytes | None = None
    filename = str(excel.get("filename") or inputs.get("excel_filename") or "workbook.xlsx")
    if path and Path(path).is_file():
        data = Path(path).read_bytes()
        filename = Path(path).name
    elif url:
        data = _fetch_bytes(url)
        parsed = urlparse(url)
        filename = Path(parsed.path).name or filename
    if not data:
        raise FileNotFoundError("excel artifact is missing")
    session_id = new_session_id()
    directory = session_dir(session_id, create=True)
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
        payload={"agent_id": "excel_extractor", "case_id": case_id},
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
    case_id = str(task.get("case_id") or "")
    task_id = str(task.get("task_id") or "")
    activity = ""
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    activity = str(inputs.get("activity_base_url") or ACTIVITY)
    _post_event(
        case_id,
        {
            "kind": "agent.accepted",
            "actor": "excel_extractor",
            "agent_id": "excel_extractor",
            "task_id": task_id,
            "status_message": "Разбираю Excel",
            "_activity_base": activity,
        },
    )
    try:
        session_id, filename = _load_excel(task)
    except FileNotFoundError as exc:
        result = {
            "task_id": task_id,
            "status": "needs_input",
            "message": "Нет Excel-файла для извлечения",
            "data": {},
            "artifacts": {},
            "issues": [{"type": "missing_excel", "detail": str(exc)}],
            "assumptions": [],
            "requests": [{"question_id": "Q-excel", "question": "Приложите workbook .xlsx", "options": []}],
        }
        _post_event(
            case_id,
            {
                "kind": "agent.result",
                "actor": "excel_extractor",
                "agent_id": "excel_extractor",
                "task_id": task_id,
                "status": "needs_input",
                "status_message": result["message"],
                "_activity_base": activity,
            },
        )
        return result
    except Exception as exc:
        _post_event(
            case_id,
            {
                "kind": "agent.failed",
                "actor": "excel_extractor",
                "agent_id": "excel_extractor",
                "task_id": task_id,
                "status": "failed",
                "status_message": str(exc)[:400],
                "_activity_base": activity,
            },
        )
        return {
            "task_id": task_id,
            "status": "failed",
            "message": str(exc)[:400],
            "data": {},
            "artifacts": {},
            "issues": [{"type": "excel_load_failed", "detail": str(exc)[:400]}],
            "assumptions": [],
            "requests": [],
        }

    with locked_session(session_id):
        state = load_state(session_id)
        detect = execute_tool(state, "detect_tables", {})
        tables = []
        raw = detect.get("result") if isinstance(detect, dict) else {}
        if isinstance(raw, dict):
            tables = raw.get("tables") or raw.get("detected_tables") or []
        if not isinstance(tables, list):
            tables = []
        _post_event(
            case_id,
            {
                "kind": "agent.progress",
                "actor": "excel_extractor",
                "agent_id": "excel_extractor",
                "task_id": task_id,
                "status_message": f"Нашёл таблиц: {len(tables)}. Читаю скважины и даты",
                "_activity_base": activity,
            },
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
            payload = queried.get("result") if isinstance(queried, dict) else queried
            preview: list[dict[str, Any]] = []
            columns: list[str] = []
            if isinstance(payload, dict):
                columns, preview = _query_rows(state, session_id, payload)
            rows_out.append(
                {
                    "table_id": table_id,
                    "columns": columns,
                    "preview": preview[:200],
                    "row_count": len(preview),
                    "records": preview,
                }
            )
        snapshot = load_state(session_id)
    facts = commissioning_facts(
        [
            {"columns": item.get("columns") or [], "preview": item.get("records") or item.get("preview") or []}
            for item in rows_out
        ]
    )
    for item in rows_out:
        item.pop("records", None)
    result = {
        "task_id": task_id,
        "status": "completed",
        "message": (
            f"Извлечено таблиц: {len(table_ids)} из {filename}"
            + (f", фактов скважина+дата: {len(facts)}" if facts else "")
        ),
        "data": {
            "excel_table": table_ids[0] if table_ids else None,
            "table_ids": table_ids,
            "normalized_rows": rows_out,
            "facts": facts,
            "session_id": session_id,
            "file_name": filename,
        },
        "artifacts": {"excel_session": session_id},
        "issues": [],
        "assumptions": [],
        "requests": [],
    }
    _post_event(
        case_id,
        {
            "kind": "agent.result",
            "actor": "excel_extractor",
            "agent_id": "excel_extractor",
            "task_id": task_id,
            "status": "completed",
            "status_message": result["message"],
            "payload": {"tables": len(table_ids)},
            "_activity_base": activity,
        },
    )
    return result
