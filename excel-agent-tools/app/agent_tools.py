"""LLM tools of the Excel Extractor agent: the LLM chooses table and columns, extraction is deterministic.

Registered on ``agent.tools`` (``app/agent.py``) next to the workbook discovery tools of ``excel_tools.py``;
the n8n workflow calls them through ``POST /agent-tools/{name}``.

* ``extract_commissioning(table_id, well_column, date_column)`` → facts «скважина — дата ввода»;
* ``extract_well_parameters(table_id, well_column, mapping)`` → per-well parameters of new wells;
* ``ask_engineer(question, options, topic)`` — the only way the LLM asks a human (prose, validated).

Argument problems raise ``ToolError`` for the LLM (``spec_incomplete``, ``table_not_found``,
``column_not_found``, ``column_not_dates``, ``no_rows``, ``too_many_attempts``) — never a question to the
engineer. Parts (dates, parameters) accumulate into one ``completed`` result via ``_merge_result``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from mas_agent_kit import (
    ToolError,
    engineer_request_from_args,
    human_text_problems,  # noqa: F401  (re-exported: tests import it from here)
    list_preview,
    parse_jsonish,
    plural_ru,
)

from .agent import agent
from .excel_tools import _record, _rows_for_table, _schema, _table
from .tools import tool

MAX_TOOL_REPEATS = 3
# Canonical engineering facts for a new well (Schedule Builder renders WELSPECS / COMPDATMD / WCONPROD
# from them; see schedule-builder-service timeline_ops.NEW_WELL_FACT_COLUMNS). Anything else in the
# row is passed through under its original header.
NEW_WELL_FIELDS = (
    "well",
    "date",
    "group",
    "phase",
    "i",
    "j",
    "md_top",
    "md_bot",
    "diameter",
    "control",
    "rate",
    "bhp",
    "thp",
    "vfp_table",
    "welltrack_include",
)


# --------------------------------------------------------------------------------------------------
# Value normalisation
# --------------------------------------------------------------------------------------------------


def _norm_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("ё", "е"))


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


_TNAV_DATE_RE = re.compile(r"^\d{1,2}\s+['\"]?[A-Za-z]{3}['\"]?\s+\d{4}$")


def _as_date(value: Any) -> tuple[Any, bool]:
    """Normalise a cell to a date string; return (value, recognised)."""
    if value in (None, ""):
        return None, False
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d"), True
    if hasattr(value, "strftime") and not isinstance(value, str):
        try:
            return value.strftime("%Y-%m-%d"), True
        except Exception:  # noqa: BLE001
            return str(value), False
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", text):
        return text[:10], True
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat(), True
        except ValueError:
            return text, False
    if _TNAV_DATE_RE.match(text):
        return text, True
    return text, False


def _match_column(columns: list[str], wanted: Any) -> str | None:
    key = _norm_key(wanted)
    if not key:
        return None
    for col in columns:
        if _norm_key(col) == key:
            return str(col)
    hits = [str(col) for col in columns if key in _norm_key(col)]
    return hits[0] if len(hits) == 1 else None


def _args_error(code: str, message: str, **details: Any) -> ToolError:
    """Argument-level problems are for the LLM (fix the call), never for the engineer."""
    return ToolError(code, message, details)


def _available_tables(state: dict[str, Any]) -> list[dict[str, Any]]:
    tables = state.get("tables") if isinstance(state.get("tables"), dict) else {}
    return [
        {"table_id": t.get("table_id"), "sheet": t.get("sheet"), "range": t.get("range"), "columns": (t.get("columns") or [])[:24]}
        for t in tables.values()
        if isinstance(t, dict)
    ]


def _resolve_table(state: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _table(state, str(args.get("table_id") or "").strip() or None)
    except ToolError as exc:
        raise _args_error(
            "table_not_found",
            "Такой таблицы в инвентаре нет. Возьми table_id из available_tables (или вызови detect_tables). Это ошибка вызова, инженера спрашивать не нужно.",
            available_tables=_available_tables(state),
        ) from exc


def _repeat_guard(state: dict[str, Any], name: str) -> None:
    agent.tools.repeat_guard(state, name, limit=MAX_TOOL_REPEATS, hint="Если данных в книге нет — спроси инженера через ask_engineer, иначе заверши ответ.")


def _merge_result(state: dict[str, Any], part: str, summary: str, data: dict[str, Any]) -> dict[str, Any]:
    """Accumulate extraction parts (dates, well parameters) into one completed agent result."""
    payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
    existing = state.get("result") if isinstance(state.get("result"), dict) else {}
    parts = existing.get("data", {}).get("parts") if isinstance(existing.get("data"), dict) else None
    parts = dict(parts) if isinstance(parts, dict) else {}
    parts[part] = summary
    merged: dict[str, Any] = dict(existing.get("data") or {}) if isinstance(existing.get("data"), dict) else {}
    merged.update(data)
    merged["parts"] = parts
    merged["session_id"] = str(state.get("session_id") or "")
    merged["file_name"] = str(state.get("file_name") or "")
    merged["files"] = list(payload.get("files") or [])
    result = agent.new_result(
        state,
        "completed",
        " ".join(parts[k] for k in ("commissioning", "well_parameters") if k in parts).strip(),
        data=merged,
        artifacts={"excel_session": str(state.get("session_id") or "")},
        assumptions=list(existing.get("assumptions") or []),
    )
    return agent.store_result(state, result)


# --------------------------------------------------------------------------------------------------
# Extraction tools (LLM chooses table and columns; extraction itself is deterministic)
# --------------------------------------------------------------------------------------------------


_wells_preview = list_preview
_plural = plural_ru


_FIELD_LABELS = {
    "date": "дата ввода",
    "group": "группа",
    "phase": "фаза",
    "i": "ячейка I",
    "j": "ячейка J",
    "md_top": "интервал MD",
    "md_bot": "интервал MD",
    "diameter": "диаметр",
    "control": "режим",
    "rate": "дебит",
    "bhp": "лимит BHP",
    "thp": "лимит THP",
    "vfp_table": "таблица VFP",
    "welltrack_include": "файл траектории",
}


@tool(
    _schema(
        "extract_commissioning",
        "Extract well → commissioning date facts from one table the model selected in the inventory. Deterministic; validates that the chosen columns exist and hold dates.",
        {
            "table_id": {"type": "string", "minLength": 1},
            "well_column": {"type": "string", "minLength": 1},
            "date_column": {"type": "string", "minLength": 1},
        },
        ["table_id", "well_column", "date_column"],
    )
)
def extract_commissioning_tool(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    _repeat_guard(state, "extract_commissioning")
    missing = [k for k in ("table_id", "well_column", "date_column") if not str(args.get(k) or "").strip()]
    if missing:
        raise _args_error(
            "spec_incomplete",
            "Укажи table_id, well_column и date_column из инвентаря (inspect.tables: columns и sample). Это ошибка вызова, инженера спрашивать не нужно.",
            missing=missing,
            where_to_find={
                "table_id": "inspect.tables[].table_id",
                "well_column": "колонка со скважинами в inspect.tables[].columns",
                "date_column": "колонка с новой датой ввода (не baseline) в inspect.tables[].columns",
            },
            available_tables=_available_tables(state),
        )
    table = _resolve_table(state, args)
    columns = [str(c) for c in (table.get("columns") or [])]
    well_col = _match_column(columns, args.get("well_column"))
    date_col = _match_column(columns, args.get("date_column"))
    bad = {name: str(args.get(name)) for name, col in (("well_column", well_col), ("date_column", date_col)) if not col}
    if bad:
        raise _args_error(
            "column_not_found",
            "Колонки с такими именами в таблице нет. Возьми точное имя из available_columns. Это ошибка вызова, инженера спрашивать не нужно.",
            not_found=bad,
            available_columns=columns,
        )
    if well_col == date_col:
        raise _args_error("column_not_found", "well_column и date_column должны быть разными колонками.", available_columns=columns)
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    non_empty = 0
    unrecognised: list[str] = []
    skipped_empty = 0
    for row in _rows_for_table(state, table):
        rec = _record(columns, row)
        well = _as_well(rec.get(well_col))
        raw_date = rec.get(date_col)
        if raw_date in (None, ""):
            skipped_empty += 1
            continue
        value, recognised = _as_date(raw_date)
        non_empty += 1
        if not recognised:
            if len(unrecognised) < 5:
                unrecognised.append(str(raw_date)[:40])
            continue
        if not well:
            skipped_empty += 1
            continue
        key = well.casefold()
        if key in seen:
            continue
        seen.add(key)
        facts.append({"well": well, "date": value})
    if non_empty and len(unrecognised) * 2 > non_empty:
        raise _args_error(
            "column_not_dates",
            "Выбранная колонка не содержит дат. Выбери другую колонку (см. sample) или спроси инженера, если колонки с датами нет.",
            date_column=date_col,
            sample_values=unrecognised,
            available_columns=columns,
        )
    if not facts:
        raise _args_error(
            "no_rows",
            "В выбранных колонках нет ни одной пары «скважина — дата». Проверь колонки по sample или выбери другую таблицу.",
            available_columns=columns,
        )
    dates = sorted(str(f["date"]) for f in facts)
    wells = [str(f["well"]) for f in facts]
    span = f"на {dates[0]}" if dates[0] == dates[-1] else f"с {dates[0]} по {dates[-1]}"
    summary = f"Даты ввода: {_plural(len(facts), 'скважина', 'скважины', 'скважин')} ({_wells_preview(wells)}), {span}."
    data = {
        "facts": facts,
        "preview": facts[:10],
        "total_count": len(facts),
        "excel_table": table.get("table_id"),
        "table_ids": [table.get("table_id")],
        "commissioning_source": {
            "table_id": table.get("table_id"),
            "sheet": table.get("sheet"),
            "range": table.get("range"),
            "well_column": well_col,
            "date_column": date_col,
            "rows_skipped": skipped_empty,
            "unrecognised_dates": unrecognised,
        },
    }
    result = _merge_result(state, "commissioning", summary, data)
    agent.activity_for_state(state).progress(summary, status="completed")
    return {"status": result["status"], "message": summary, "facts_count": len(facts), "preview": facts[:5]}


def _mapping_from_args(raw: Any) -> dict[str, str]:
    parsed = parse_jsonish(raw) if not isinstance(raw, dict) else raw
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        field = _norm_key(key).replace(" ", "_")
        if field in NEW_WELL_FIELDS and str(value or "").strip():
            out[field] = str(value).strip()
    return out


def _cell_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return value.strip()
    return value


@tool(
    _schema(
        "extract_well_parameters",
        "Extract a per-well parameters table (group, MD interval, diameter, control, rate, BHP, VFP, trajectory file…) for new wells. Model selects the table, the well column and an optional column→field mapping; extraction is deterministic.",
        {
            "table_id": {"type": "string", "minLength": 1},
            "well_column": {"type": "string", "minLength": 1},
            "mapping": {"type": ["object", "string"]},
        },
        ["table_id", "well_column"],
    )
)
def extract_well_parameters_tool(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    _repeat_guard(state, "extract_well_parameters")
    missing = [k for k in ("table_id", "well_column") if not str(args.get(k) or "").strip()]
    if missing:
        raise _args_error(
            "spec_incomplete",
            "Укажи table_id и well_column из инвентаря; mapping — JSON {поле: колонка} для полей "
            + ", ".join(NEW_WELL_FIELDS[1:])
            + ". Это ошибка вызова, инженера спрашивать не нужно.",
            missing=missing,
            available_tables=_available_tables(state),
        )
    table = _resolve_table(state, args)
    columns = [str(c) for c in (table.get("columns") or [])]
    well_col = _match_column(columns, args.get("well_column"))
    if not well_col:
        raise _args_error(
            "column_not_found",
            "Колонки со скважинами с таким именем нет. Возьми точное имя из available_columns.",
            not_found={"well_column": str(args.get("well_column"))},
            available_columns=columns,
        )
    mapping = _mapping_from_args(args.get("mapping"))
    resolved_map: dict[str, str] = {}
    bad: dict[str, str] = {}
    for field, col_name in mapping.items():
        col = _match_column(columns, col_name)
        if col:
            resolved_map[field] = col
        else:
            bad[field] = col_name
    if bad:
        raise _args_error(
            "column_not_found",
            "В mapping указаны колонки, которых нет в таблице. Возьми точные имена из available_columns.",
            not_found=bad,
            available_columns=columns,
        )
    mapped_cols = set(resolved_map.values()) | {well_col}
    rows_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _rows_for_table(state, table):
        rec = _record(columns, row)
        well = _as_well(rec.get(well_col))
        if not well or well.casefold() in seen:
            continue
        seen.add(well.casefold())
        out: dict[str, Any] = {"well": well}
        for field, col in resolved_map.items():
            value = rec.get(col)
            if value in (None, ""):
                continue
            out[field] = _as_date(value)[0] if field == "date" else _cell_value(value)
        for col in columns:
            if col in mapped_cols:
                continue
            value = rec.get(col)
            if value in (None, ""):
                continue
            out[col] = _cell_value(value)
        rows_out.append(out)
    if not rows_out:
        raise _args_error("no_rows", "В таблице нет строк со скважинами в выбранной колонке.", available_columns=columns)
    wells = [str(r["well"]) for r in rows_out]
    labels: list[str] = []
    for field in resolved_map:
        label = _FIELD_LABELS.get(field, "")
        if label and label not in labels:
            labels.append(label)
    extra = len([c for c in columns if c not in mapped_cols])
    described = ", ".join(labels) if labels else ""
    if extra:
        described = (described + ", " if described else "") + _plural(extra, "прочая колонка", "прочие колонки", "прочих колонок")
    summary = f"Параметры новых скважин: {_plural(len(rows_out), 'скважина', 'скважины', 'скважин')} ({_wells_preview(wells)})" + (
        f" — {described}." if described else "."
    )
    data = {
        "new_wells": rows_out,
        "new_wells_source": {
            "table_id": table.get("table_id"),
            "sheet": table.get("sheet"),
            "range": table.get("range"),
            "well_column": well_col,
            "mapping": resolved_map,
            "columns": columns,
        },
    }
    result = _merge_result(state, "well_parameters", summary, data)
    agent.activity_for_state(state).progress(summary, status="completed")
    return {"status": result["status"], "message": summary, "wells_count": len(rows_out), "preview": rows_out[:3]}


# --------------------------------------------------------------------------------------------------
# ask_engineer — the only way the LLM asks a human (prose, validated)
# --------------------------------------------------------------------------------------------------

@tool(
    _schema(
        "ask_engineer",
        "Ask the engineer one question in plain Russian when the inventory cannot resolve which table/column to use. Options are shown as buttons.",
        {
            "question": {"type": "string", "minLength": 1},
            "options": {"type": ["array", "string"]},
            "topic": {"type": "string"},
        },
        ["question"],
    )
)
def ask_engineer_tool(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """The only way the LLM asks a human: prose + option buttons, validated by the kit gate."""
    state = ctx["state"]
    request = engineer_request_from_args(args, default_topic="excel", default_files=("xlsx",))
    existing = state.get("result") if isinstance(state.get("result"), dict) else {}
    result = agent.new_result(
        state,
        "needs_input",
        request["question"],
        data={**(existing.get("data") if isinstance(existing.get("data"), dict) else {}), "asked_by": "excel_extractor_llm"},
        artifacts={"excel_session": str(state.get("session_id") or "")},
        issues=[{"type": "engineer_input_required", "topic": request["question_id"][2:]}],
        requests=[request],
    )
    agent.store_result(state, result)
    agent.activity_for_state(state).progress("Нужно уточнение инженера: " + request["question"][:200], status="needs_input")
    return {"status": "needs_input", "message": request["question"]}
