"""LLM tools of the Excel Extractor agent: the LLM chooses table and columns, extraction is deterministic.

Registered on ``agent.tools`` (``app/agent.py``) next to the workbook discovery tools of ``excel_tools.py``;
the n8n workflow calls them through ``POST /agent-tools/{name}``.

* ``extract_commissioning(table_id, well_column, date_column)`` → facts «скважина — дата ввода»;
* ``extract_well_parameters(table_id, well_column, mapping)`` → per-well parameters of new wells;
* ``extract_table(table_id, name, columns, …)`` → any other table as a **dataset** (``mas_agent_kit.dataset``):
  typed rows under ``data[<name>]`` plus a JSON artifact; the shape the orchestrator asked for comes in
  ``inputs.expected_output`` and the tool reports which requested fields the table could not provide;
* ``ask_engineer(question, options, topic)`` — the only way the LLM asks a human (prose, validated).

Argument problems raise ``ToolError`` for the LLM (``spec_incomplete``, ``table_not_found``,
``column_not_found``, ``column_not_dates``, ``no_rows``, ``name_reserved``, ``too_many_attempts``) — never a
question to the engineer. Parts (dates, parameters, datasets) accumulate into one ``completed`` result via
``_merge_result``; its ``next_step`` tells the model which expected datasets are still missing.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from mas_agent_kit import (
    ToolError,
    dataset_entry,
    engineer_request_from_args,
    expected_names,
    expected_output,
    human_text_problems,  # noqa: F401  (re-exported: tests import it from here)
    list_preview,
    parse_jsonish,
    plural_ru,
)
from mas_agent_kit.dataset import artifact_id_for, dataset_json_bytes, dataset_name

from . import dataset_extract as ds
from .agent import agent
from .excel_tools import _record, _rows_for_table, _schema, _table
from .tools import tool

MAX_TOOL_REPEATS = 3
# ``data`` keys with a fixed meaning for consumers / bookkeeping: a dataset may not take them.
RESERVED_DATA_KEYS = frozenset(
    {"facts", "new_wells", "parts", "session_id", "file_name", "files", "preview", "total_count", "excel_table", "table_ids", "commissioning_source", "new_wells_source", "omitted_keys", "asked_by", "expected"}
)
# Which ``data`` key each result part fixes — for the expected_output coverage.
PART_DATA_KEY = {"commissioning": "facts", "well_parameters": "new_wells"}
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


def _produced_keys(parts: dict[str, Any]) -> list[str]:
    """``data`` keys the accumulated parts fixed: facts / new_wells / dataset names (``dataset:<name>``)."""
    out: list[str] = []
    for part in parts:
        key = PART_DATA_KEY.get(part) or (part.split(":", 1)[1] if part.startswith("dataset:") else "")
        if key and key not in out:
            out.append(key)
    return out


def expected_coverage(state: dict[str, Any], parts: dict[str, Any]) -> dict[str, Any]:
    """What the orchestrator asked for (``inputs.expected_output.datasets``) versus what the tools fixed."""
    requested = expected_names(state.get("inputs"))
    produced = _produced_keys(parts)
    return {"requested": requested, "produced": produced, "missing": [n for n in requested if n not in produced]}


def _expected_descriptions(state: dict[str, Any]) -> dict[str, str]:
    exp = expected_output(state.get("inputs"))
    return {d["name"]: str(d.get("description") or "") for d in exp.get("datasets") or []}


def remaining_hint(state: dict[str, Any], coverage: dict[str, Any]) -> str:
    """``next_step`` for the model after a part was fixed: stop, or extract the datasets still expected."""
    missing = list(coverage.get("missing") or [])
    if not missing:
        return ""
    descriptions = _expected_descriptions(state)
    names = ", ".join(f"{n}" + (f" ({descriptions[n]})" if descriptions.get(n) else "") for n in missing)
    return (
        f"Часть результата зафиксирована. Оркестратор ещё ожидает наборы данных: {names}. "
        "Извлеки их следующими вызовами (facts → extract_commissioning, new_wells → extract_well_parameters, остальные → extract_table с тем же name), "
        "затем заверши ответ одним предложением. Если такой таблицы в книге нет — так и напиши в ответе, инженера спрашивай только если без него нельзя выбрать таблицу."
    )


def _merge_result(state: dict[str, Any], part: str, summary: str, data: dict[str, Any], artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Accumulate extraction parts (dates, well parameters, datasets) into one completed agent result.

    Returns the model view of the stored result; ``next_step`` names the expected datasets still missing.
    """
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
    coverage = expected_coverage(state, parts)
    if coverage["requested"]:
        merged["expected"] = coverage
    ordered = [k for k in ("commissioning", "well_parameters") if k in parts] + [k for k in parts if k not in PART_DATA_KEY]
    cards = {k: v for k, v in (existing.get("artifacts") or {}).items()} if isinstance(existing.get("artifacts"), dict) else {}
    cards.update(artifacts or {})
    cards["excel_session"] = str(state.get("session_id") or "")
    result = agent.new_result(
        state,
        "completed",
        " ".join(parts[k] for k in ordered).strip(),
        data=merged,
        artifacts=cards,
        assumptions=list(existing.get("assumptions") or []),
    )
    view = agent.store_result(state, result)
    hint = remaining_hint(state, coverage)
    if hint:
        view["next_step"] = hint
    return view


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
                "date_column": "колонка с новой датой ввода (не из исходного .INC) в inspect.tables[].columns",
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
    return {"status": result["status"], "message": summary, "facts_count": len(facts), "preview": facts[:5], "next_step": result.get("next_step", "")}


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
    return {"status": result["status"], "message": summary, "wells_count": len(rows_out), "preview": rows_out[:3], "next_step": result.get("next_step", "")}


# --------------------------------------------------------------------------------------------------
# extract_table — any table as a dataset (the flexible output; shape comes from the orchestrator)
# --------------------------------------------------------------------------------------------------


def _dataset_title(state: dict[str, Any], args: dict[str, Any], table: dict[str, Any], name: str) -> str:
    """Russian title for the feed: the model's title, else the description the orchestrator put on this dataset."""
    del table  # detect_tables preamble is not a dataset title
    for candidate in (args.get("title"), args.get("description"), _expected_descriptions(state).get(name)):
        text = str(candidate or "").strip()
        if text and not human_text_problems(text, min_length=1):
            return text[:120]
    return ""


def _requested_fields(state: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """Field descriptors the orchestrator asked for under this dataset name (``[{name, description?, type?}]``)."""
    for d in expected_output(state.get("inputs")).get("datasets") or []:
        if d.get("name") == name:
            return [dict(f) for f in d.get("fields") or []]
    return []


def _upload_dataset(state: dict[str, Any], entry: dict[str, Any], rows: list[dict[str, Any]], summary: str) -> dict[str, Any] | None:
    """Full rows → Activity JSON artifact (survives the orchestrator's state budget; downloadable in the UI)."""
    activity = agent.activity_for_state(state)
    if not activity.configured:
        return None
    try:
        card = activity.upload(artifact_id_for(entry["name"]), f"{entry['name']}.json", dataset_json_bytes(entry, rows), "application/json", summary=summary)
    except Exception as exc:  # noqa: BLE001 — the inline rows / preview still carry the result
        activity.trace("Не удалось сохранить набор данных как артефакт", level="warn", dataset=entry["name"], error=str(exc)[:300])
        return None
    return {k: v for k, v in card.items() if k != "download_path"}


@tool(
    _schema(
        "extract_table",
        "Extract any table as a named dataset: typed rows under data[name] plus a JSON artifact. The model names the table, the dataset and (optionally) field→column mapping, types, filters, a key field and an unpivot rule; extraction is deterministic.",
        {
            "table_id": {"type": "string", "minLength": 1},
            "name": {"type": "string", "minLength": 1},
            "columns": {"type": ["object", "array", "string"]},
            "title": {"type": "string"},
            "types": {"type": ["object", "string"]},
            "filters": {"type": ["array", "object", "string"]},
            "key_field": {"type": "string"},
            "unpivot": {"type": ["object", "string"]},
        },
        ["table_id", "name"],
    )
)
def extract_table_tool(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    _repeat_guard(state, "extract_table")
    if not str(args.get("table_id") or "").strip():
        raise _args_error(
            "spec_incomplete",
            "Укажи table_id из инвентаря и name — латинское имя набора данных (то, что просил оркестратор в expected_output, иначе своё: well_events, oil_rates). Это ошибка вызова, инженера спрашивать не нужно.",
            missing=["table_id"],
            available_tables=_available_tables(state),
        )
    name = dataset_name(args.get("name"))
    if not name:
        raise _args_error(
            "spec_incomplete",
            "name — латинский идентификатор набора данных (например well_events, oil_rates); если оркестратор назвал набор в expected_output — используй то имя. Это ошибка вызова, инженера спрашивать не нужно.",
            missing=["name"],
            got=str(args.get("name") or ""),
        )
    if name in RESERVED_DATA_KEYS:
        raise _args_error(
            "name_reserved",
            "Это имя занято: facts извлекает extract_commissioning, new_wells — extract_well_parameters; для прочих таблиц выбери другое имя набора.",
            name=name,
            use_instead={"facts": "extract_commissioning", "new_wells": "extract_well_parameters"}.get(name, "другое имя"),
        )
    table = _resolve_table(state, args)
    columns = [str(c) for c in (table.get("columns") or [])]
    mapping, missing_cols = ds.resolve_columns(columns, args.get("columns"))
    if missing_cols:
        raise _args_error(
            "column_not_found",
            "В columns указаны колонки, которых нет в таблице. Возьми точные имена из available_columns. Это ошибка вызова, инженера спрашивать не нужно.",
            not_found=missing_cols,
            available_columns=columns,
        )
    if not mapping:
        raise _args_error("spec_incomplete", "В таблице не осталось колонок для набора данных.", available_columns=columns)
    filters = ds.resolve_filters(columns, mapping, args.get("filters"))
    unpivot = ds.resolve_unpivot(columns, mapping, args.get("unpivot"))
    key_field = dataset_name(args.get("key_field"))
    known_fields = set(mapping) | ({unpivot.name_field, unpivot.value_field} if unpivot else set())
    if key_field and key_field not in known_fields:
        raise _args_error("column_not_found", "key_field должен быть одним из полей набора (ключей columns).", key_field=key_field, fields=sorted(known_fields))
    # Types: the model's declaration > the type the orchestrator asked for in expected_output > inference.
    requested = _requested_fields(state, name)
    types = {f["name"]: f["type"] for f in requested if f.get("type") and f["name"] in known_fields}
    types.update(ds.resolve_types(known_fields, args.get("types")))
    built = ds.build_dataset((_record(columns, row) for row in _rows_for_table(state, table)), mapping, types=types, filters=filters, key_field=key_field, unpivot=unpivot)
    if not built.rows:
        raise _args_error(
            "no_rows",
            "После выбора колонок и фильтров в таблице не осталось ни одной строки. Проверь columns/filters по sample или выбери другую таблицу.",
            available_columns=columns,
            **({"filtered_out": built.issues["filtered_out"]} if "filtered_out" in built.issues else {}),
        )
    produced = {f["name"] for f in built.fields}
    requested_missing = [f["name"] for f in requested if f["name"] not in produced]
    issues = dict(built.issues)
    if requested_missing:
        issues["requested_fields_missing"] = requested_missing
    title = _dataset_title(state, args, table, name)
    source = {"table_id": table.get("table_id"), "sheet": table.get("sheet"), "range": table.get("range"), "columns": columns}
    entry = dataset_entry(name, built.fields, built.rows, title=title, source=source, issues=issues)
    field_words = ", ".join(f["name"] for f in built.fields)
    summary = f"Извлёк набор данных{f' «{title}»' if title else ''}: {_plural(len(built.rows), 'строка', 'строки', 'строк')}, {_plural(len(built.fields), 'поле', 'поля', 'полей')}."
    if requested_missing:
        # Field names are identifiers — they stay in issues (data); the feed line only says how many.
        summary += f" В таблице не нашлось {_plural(len(requested_missing), 'запрошенного поля', 'запрошенных полей', 'запрошенных полей')}."
    card = _upload_dataset(state, entry, built.rows, summary)
    if card:
        entry["artifact_id"] = str(card.get("artifact_id") or entry.get("artifact_id") or "")
    result = _merge_result(state, f"dataset:{name}", summary, {name: entry}, artifacts={card["artifact_id"]: card} if card else None)
    agent.activity_for_state(state).progress(summary, status="completed")
    return {
        "status": result["status"],
        "message": summary,
        "dataset": name,
        "row_count": len(built.rows),
        "fields": built.fields,
        "preview": built.rows[:3],
        "issues": issues,
        "field_names": field_words,
        "next_step": result.get("next_step", ""),
    }


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
    view = agent.store_result(state, result)
    agent.activity_for_state(state).progress("Нужно уточнение инженера: " + request["question"][:200], status="needs_input")
    return {"status": "needs_input", "message": request["question"], "next_step": view.get("next_step", "")}
