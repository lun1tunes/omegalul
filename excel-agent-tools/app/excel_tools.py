"""Deterministic Excel inspection, query and export tools.

No LLM or orchestration logic belongs here. Tool responses are deliberately compact so
that an external agent can decide the next call without receiving the workbook itself.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .sessions import ARTIFACT_RE, session_file
from .tools import ToolError, tool

MAX_PREVIEW_ROWS = int(os.getenv("MAX_PREVIEW_ROWS", "100"))
MAX_QUERY_PREVIEW_ROWS = int(os.getenv("MAX_QUERY_PREVIEW_ROWS", "100"))
MAX_DISTINCT_VALUES = 500
SUPPORTED_OPENPYXL_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm"}
SUPPORTED_SUFFIXES = SUPPORTED_OPENPYXL_SUFFIXES | {".xls"}


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _file_path(state: dict[str, Any]) -> Path:
    stored_path = state.get("file_path")
    if not isinstance(stored_path, str):
        raise ToolError("SESSION_FILE_MISSING", "Session input file is unavailable")
    try:
        path = session_file(state["session_id"], stored_path)
    except ValueError as error:
        raise ToolError("SESSION_FILE_MISSING", "Session input file is unavailable") from error
    if not path.is_file():
        raise ToolError("SESSION_FILE_MISSING", "Session input file is unavailable")
    return path


def _suffix(state: dict[str, Any]) -> str:
    suffix = Path(str(state.get("file_path", ""))).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ToolError("UNSUPPORTED_FILE_TYPE", "Only .xlsx, .xlsm, .xltx and .xls Excel files are supported")
    return suffix


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    # numpy/pandas scalar and Decimal support, without hard importing those packages.
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (ValueError, TypeError):
            pass
    return str(value)


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _nonempty_count(row: Iterable[Any]) -> int:
    return sum(not _is_empty(value) for value in row)


def _trim_row(row: tuple[Any, ...] | list[Any], max_col: int) -> list[Any]:
    values = list(row[:max_col])
    while values and _is_empty(values[-1]):
        values.pop()
    return values


def _xlsx_sheet_names_and_dimensions(path: Path) -> list[dict[str, Any]]:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    except Exception as error:
        raise ToolError("WORKBOOK_READ_ERROR", "Cannot read Excel workbook") from error
    try:
        return [
            {
                "name": worksheet.title,
                "max_row": worksheet.max_row or 0,
                "max_column": worksheet.max_column or 0,
                "state": worksheet.sheet_state,
            }
            for worksheet in workbook.worksheets
        ]
    finally:
        workbook.close()


def _xls_frames(path: Path) -> dict[str, Any]:
    try:
        import pandas as pd

        with pd.ExcelFile(path, engine="calamine") as excel_file:
            return {
                name: pd.read_excel(excel_file, sheet_name=name, header=None, dtype=object)
                for name in excel_file.sheet_names
            }
    except Exception as error:
        raise ToolError("WORKBOOK_READ_ERROR", "Cannot read .xls workbook") from error


def _sheets(state: dict[str, Any]) -> list[dict[str, Any]]:
    path = _file_path(state)
    if _suffix(state) in SUPPORTED_OPENPYXL_SUFFIXES:
        return _xlsx_sheet_names_and_dimensions(path)
    frames = _xls_frames(path)
    return [
        {"name": name, "max_row": int(frame.shape[0]), "max_column": int(frame.shape[1]), "state": "visible"}
        for name, frame in frames.items()
    ]


def _ensure_sheet(state: dict[str, Any], sheet: str) -> None:
    available = [item["name"] for item in _sheets(state)]
    if sheet not in available:
        raise ToolError("SHEET_NOT_FOUND", f"Sheet {sheet} not found", {"available_sheets": available})


def _iter_sheet_rows(state: dict[str, Any], sheet: str, min_row: int = 1, max_row: int | None = None) -> Iterator[tuple[int, list[Any]]]:
    path = _file_path(state)
    suffix = _suffix(state)
    if suffix in SUPPORTED_OPENPYXL_SUFFIXES:
        try:
            workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
            if sheet not in workbook.sheetnames:
                raise ToolError("SHEET_NOT_FOUND", f"Sheet {sheet} not found", {"available_sheets": workbook.sheetnames})
            worksheet = workbook[sheet]
            final_row = max_row if max_row is not None else worksheet.max_row
            for index, row in enumerate(
                worksheet.iter_rows(min_row=min_row, max_row=final_row, values_only=True), start=min_row
            ):
                yield index, list(row)
        except ToolError:
            raise
        except Exception as error:
            raise ToolError("WORKBOOK_READ_ERROR", "Cannot read Excel workbook") from error
        finally:
            if "workbook" in locals():
                workbook.close()
        return

    frames = _xls_frames(path)
    if sheet not in frames:
        raise ToolError("SHEET_NOT_FOUND", f"Sheet {sheet} not found", {"available_sheets": list(frames)})
    frame = frames[sheet]
    end = min(max_row or len(frame), len(frame))
    for index in range(max(1, min_row), end + 1):
        yield index, [_json_value(value) for value in frame.iloc[index - 1].tolist()]


def _headers(header_values: list[Any], max_col: int) -> list[str]:
    result: list[str] = []
    used: Counter[str] = Counter()
    for index in range(max_col):
        raw = header_values[index] if index < len(header_values) else None
        base = str(raw).strip() if not _is_empty(raw) else f"Column {get_column_letter(index + 1)}"
        used[base] += 1
        result.append(base if used[base] == 1 else f"{base} ({used[base]})")
    return result


def _column_index(columns: list[str], requested: str) -> int:
    if requested in columns:
        return columns.index(requested)
    folded = requested.strip().casefold()
    matches = [index for index, column in enumerate(columns) if column.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    raise ToolError("COLUMN_NOT_FOUND", f"Column {requested} not found", {"available_columns": columns})


def _table(state: dict[str, Any], table_id: str) -> dict[str, Any]:
    table = state.get("tables", {}).get(table_id)
    if not table:
        raise ToolError("TABLE_NOT_FOUND", f"Table {table_id} not found", {"available_table_ids": list(state.get("tables", {}))})
    return table


def _rows_for_table(state: dict[str, Any], table: dict[str, Any]) -> Iterator[list[Any]]:
    for row_number, row in _iter_sheet_rows(state, table["sheet"], table["header_row"] + 1, table["end_row"]):
        del row_number
        values = row[table["start_column"] - 1 : table["end_column"]]
        if not all(_is_empty(value) for value in values):
            yield values


def _record(columns: list[str], row: list[Any]) -> dict[str, Any]:
    return {column: _json_value(row[index] if index < len(row) else None) for index, column in enumerate(columns)}


def _compact_rows(rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    return rows[: max(0, min(max_rows, MAX_PREVIEW_ROWS))]


@tool(_schema("workbook_introspect", "Returns compact workbook metadata and sheet dimensions. Call this before reading sheets.", {}))
def workbook_introspect(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    del args
    state = ctx["state"]
    sheets = _sheets(state)
    result = {"file_name": state["file_name"], "sheets": sheets}
    state["workbook_meta"] = result
    state["status"] = "introspected"
    return result


@tool(
    _schema(
        "sheet_preview",
        "Returns a small rectangular preview from one sheet; it never returns the whole sheet.",
        {
            "sheet": {"type": "string", "minLength": 1},
            "start_row": {"type": "integer", "minimum": 1, "default": 1},
            "max_rows": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "max_columns": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
        },
        ["sheet"],
    )
)
def sheet_preview(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    sheet = args.get("sheet")
    if not isinstance(sheet, str) or not sheet:
        raise ToolError("INVALID_ARGUMENTS", "sheet must be a non-empty string")
    _ensure_sheet(state, sheet)
    start_row = args.get("start_row", 1)
    max_rows = args.get("max_rows", 20)
    max_columns = args.get("max_columns", 30)
    if not isinstance(start_row, int) or start_row < 1 or not isinstance(max_rows, int) or not 1 <= max_rows <= 100 or not isinstance(max_columns, int) or not 1 <= max_columns <= 100:
        raise ToolError("INVALID_ARGUMENTS", "Invalid preview bounds")

    rows: list[dict[str, Any]] = []
    for row_number, row in _iter_sheet_rows(state, sheet, start_row, start_row + max_rows - 1):
        values = [_json_value(value) for value in _trim_row(row, max_columns)]
        rows.append({"row_number": row_number, "values": values})
    return {"sheet": sheet, "start_row": start_row, "rows": rows}


@tool(
    _schema(
        "detect_tables",
        "Detects contiguous tabular regions and saves their IDs for later tools. Use optional sheet to restrict search.",
        {"sheet": {"type": "string", "minLength": 1}, "max_tables": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10}},
    )
)
def detect_tables(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    requested_sheet = args.get("sheet")
    max_tables = args.get("max_tables", 10)
    if requested_sheet is not None and (not isinstance(requested_sheet, str) or not requested_sheet):
        raise ToolError("INVALID_ARGUMENTS", "sheet must be a non-empty string")
    if not isinstance(max_tables, int) or not 1 <= max_tables <= 30:
        raise ToolError("INVALID_ARGUMENTS", "max_tables must be from 1 to 30")
    sheets = [requested_sheet] if requested_sheet else [item["name"] for item in _sheets(state)]
    if requested_sheet:
        _ensure_sheet(state, requested_sheet)

    found: list[dict[str, Any]] = []
    for sheet in sheets:
        active: dict[str, Any] | None = None
        blank_run = 0
        # A table starts at a row containing at least two nonempty header cells. It ends after a blank row.
        for row_number, row in _iter_sheet_rows(state, sheet):
            nonempty = [index for index, value in enumerate(row, start=1) if not _is_empty(value)]
            if not nonempty:
                if active:
                    blank_run += 1
                    if blank_run >= 1:
                        active["end_row"] = row_number - 1
                        found.append(active)
                        active = None
                        if len(found) >= max_tables:
                            break
                continue
            blank_run = 0
            if active is None:
                if len(nonempty) < 2:
                    continue
                active = {
                    "sheet": sheet,
                    "header_row": row_number,
                    "start_column": min(nonempty),
                    "end_column": max(nonempty),
                    "end_row": row_number,
                    "header_values": row,
                }
            else:
                active["end_row"] = row_number
                active["start_column"] = min(active["start_column"], min(nonempty))
                active["end_column"] = max(active["end_column"], max(nonempty))
        if active and len(found) < max_tables:
            found.append(active)
        if len(found) >= max_tables:
            break

    response_tables: list[dict[str, Any]] = []
    for candidate in found:
        max_col = candidate["end_column"] - candidate["start_column"] + 1
        header_source = candidate["header_values"][candidate["start_column"] - 1 : candidate["end_column"]]
        columns = _headers(header_source, max_col)
        table_id = _id("tbl")
        persisted = {
            "table_id": table_id,
            "sheet": candidate["sheet"],
            "header_row": candidate["header_row"],
            "start_column": candidate["start_column"],
            "end_column": candidate["end_column"],
            "end_row": candidate["end_row"],
            "range": f"{get_column_letter(candidate['start_column'])}{candidate['header_row']}:{get_column_letter(candidate['end_column'])}{candidate['end_row']}",
            "columns": columns,
        }
        state.setdefault("tables", {})[table_id] = persisted
        response_tables.append({key: persisted[key] for key in ("table_id", "sheet", "header_row", "range")})
    return {"tables": response_tables}


@tool(
    _schema(
        "describe_table",
        "Returns table columns, number of data rows and a small sample. Requires a table_id from detect_tables.",
        {"table_id": {"type": "string", "minLength": 1}, "sample_rows": {"type": "integer", "minimum": 0, "maximum": 20, "default": 5}},
        ["table_id"],
    )
)
def describe_table(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    table = _table(ctx["state"], args.get("table_id", ""))
    sample_rows = args.get("sample_rows", 5)
    if not isinstance(sample_rows, int) or not 0 <= sample_rows <= 20:
        raise ToolError("INVALID_ARGUMENTS", "sample_rows must be from 0 to 20")
    sample: list[dict[str, Any]] = []
    row_count = 0
    for row in _rows_for_table(ctx["state"], table):
        row_count += 1
        if len(sample) < sample_rows:
            sample.append(_record(table["columns"], row))
    return {
        "table_id": table["table_id"],
        "sheet": table["sheet"],
        "range": table["range"],
        "columns": table["columns"],
        "row_count": row_count,
        "sample_rows": sample,
    }


@tool(
    _schema(
        "list_column_values",
        "Returns bounded distinct values and counts for one column, useful to select exact filter values.",
        {"table_id": {"type": "string", "minLength": 1}, "column": {"type": "string", "minLength": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}},
        ["table_id", "column"],
    )
)
def list_column_values(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    table = _table(state, args.get("table_id", ""))
    column = args.get("column")
    limit = args.get("limit", 50)
    if not isinstance(column, str) or not column or not isinstance(limit, int) or not 1 <= limit <= MAX_DISTINCT_VALUES:
        raise ToolError("INVALID_ARGUMENTS", "Invalid column or limit")
    index = _column_index(table["columns"], column)
    counts: Counter[str] = Counter()
    examples: dict[str, Any] = {}
    null_count = 0
    for row in _rows_for_table(state, table):
        value = row[index] if index < len(row) else None
        if _is_empty(value):
            null_count += 1
            continue
        safe_value = _json_value(value)
        key = json.dumps(safe_value, ensure_ascii=False, sort_keys=True)
        counts[key] += 1
        examples.setdefault(key, safe_value)
    ordered = sorted(counts, key=lambda key: (-counts[key], str(examples[key])))[:limit]
    return {
        "table_id": table["table_id"],
        "column": table["columns"][index],
        "values": [{"value": examples[key], "count": counts[key]} for key in ordered],
        "null_count": null_count,
        "distinct_count": len(counts),
        "truncated": len(counts) > limit,
    }


def _comparable(value: Any) -> Any:
    value = _json_value(value)
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return float(value.replace(" ", "").replace(",", "."))
        except ValueError:
            return value.casefold()
    return str(value).casefold()


def _equal(left: Any, right: Any) -> bool:
    return _comparable(left) == _comparable(right)


def _matches_filter(value: Any, condition: dict[str, Any]) -> bool:
    operator = condition.get("operator", "eq")
    target = condition.get("value")
    if operator == "is_null":
        return _is_empty(value)
    if operator == "not_null":
        return not _is_empty(value)
    if operator in {"in", "not_in"}:
        if not isinstance(target, list):
            raise ToolError("INVALID_FILTER", f"Filter {operator} requires an array value")
        match = any(_equal(value, item) for item in target)
        return not match if operator == "not_in" else match
    if operator in {"contains", "not_contains"}:
        if target is None:
            raise ToolError("INVALID_FILTER", f"Filter {operator} requires value")
        match = str(_json_value(target)).casefold() in str(_json_value(value) or "").casefold()
        return not match if operator == "not_contains" else match
    if operator == "between":
        if not isinstance(target, list) or len(target) != 2:
            raise ToolError("INVALID_FILTER", "Filter between requires [lower, upper]")
        comparable = _comparable(value)
        return comparable is not None and _comparable(target[0]) <= comparable <= _comparable(target[1])
    if operator in {"eq", "neq"}:
        match = _equal(value, target)
        return not match if operator == "neq" else match
    if operator in {"gt", "gte", "lt", "lte"}:
        comparable = _comparable(value)
        target_value = _comparable(target)
        if comparable is None or target_value is None or type(comparable) is not type(target_value):
            return False
        return {"gt": comparable > target_value, "gte": comparable >= target_value, "lt": comparable < target_value, "lte": comparable <= target_value}[operator]
    raise ToolError("INVALID_FILTER", f"Unsupported filter operator {operator}", {"supported_operators": ["eq", "neq", "in", "not_in", "contains", "not_contains", "gt", "gte", "lt", "lte", "between", "is_null", "not_null"]})


def _prepare_query(table: dict[str, Any], args: dict[str, Any]) -> tuple[list[str], list[int], list[tuple[int, dict[str, Any]]]]:
    select = args.get("select") or table["columns"]
    if not isinstance(select, list) or not select or not all(isinstance(value, str) and value for value in select):
        raise ToolError("INVALID_ARGUMENTS", "select must be a non-empty list of column names")
    selected_indices = [_column_index(table["columns"], column) for column in select]
    selected_columns = [table["columns"][index] for index in selected_indices]
    filters = args.get("filters", [])
    if not isinstance(filters, list):
        raise ToolError("INVALID_FILTER", "filters must be an array")
    prepared_filters: list[tuple[int, dict[str, Any]]] = []
    for condition in filters:
        if not isinstance(condition, dict) or not isinstance(condition.get("field"), str):
            raise ToolError("INVALID_FILTER", "Each filter needs field and operator")
        prepared_filters.append((_column_index(table["columns"], condition["field"]), condition))
    return selected_columns, selected_indices, prepared_filters


def _iter_matching_records(state: dict[str, Any], table: dict[str, Any], selected_columns: list[str], selected_indices: list[int], filters: list[tuple[int, dict[str, Any]]]) -> Iterator[dict[str, Any]]:
    for row in _rows_for_table(state, table):
        if all(_matches_filter(row[index] if index < len(row) else None, condition) for index, condition in filters):
            yield {column: _json_value(row[index] if index < len(row) else None) for column, index in zip(selected_columns, selected_indices, strict=True)}


@tool(
    _schema(
        "query_table",
        "Filters a detected table and stores a bounded result set. For all matching rows use export_result afterwards.",
        {
            "table_id": {"type": "string", "minLength": 1},
            "select": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "filters": {"type": "array", "items": {"type": "object"}, "default": []},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 200},
        },
        ["table_id"],
    )
)
def query_table(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    table = _table(state, args.get("table_id", ""))
    limit = args.get("limit", 200)
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ToolError("INVALID_ARGUMENTS", "limit must be from 1 to 10000")
    columns, indices, filters = _prepare_query(table, args)
    result_id = _id("res")
    relative_path = f"results/{result_id}.jsonl"
    output_path = session_file(state["session_id"], relative_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    stored_rows = 0
    preview_rows: list[dict[str, Any]] = []
    # Keep no large table in memory; a later export can re-run the saved deterministic query for all records.
    with output_path.open("w", encoding="utf-8") as output:
        for record in _iter_matching_records(state, table, columns, indices, filters):
            row_count += 1
            if stored_rows < limit:
                output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                stored_rows += 1
                if len(preview_rows) < MAX_QUERY_PREVIEW_ROWS:
                    preview_rows.append(record)
    state.setdefault("result_sets", {})[result_id] = {
        "result_id": result_id,
        "table_id": table["table_id"],
        "columns": columns,
        "row_count": row_count,
        "stored_rows": stored_rows,
        "truncated": row_count > stored_rows,
        "path": relative_path,
        "query": {"select": columns, "filters": args.get("filters", [])},
    }
    return {
        "result_id": result_id,
        "columns": columns,
        "row_count": row_count,
        "stored_rows": stored_rows,
        "preview_rows": preview_rows,
        "truncated": row_count > stored_rows,
    }


@tool(
    _schema(
        "validate_result",
        "Validates a stored query result before finalization.",
        {"result_id": {"type": "string", "minLength": 1}, "required_columns": {"type": "array", "items": {"type": "string"}, "default": []}, "min_rows": {"type": "integer", "minimum": 0, "default": 1}},
        ["result_id"],
    )
)
def validate_result(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    result = state.get("result_sets", {}).get(args.get("result_id"))
    if not result:
        raise ToolError("RESULT_NOT_FOUND", f"Result {args.get('result_id')} not found")
    required_columns = args.get("required_columns", [])
    min_rows = args.get("min_rows", 1)
    if not isinstance(required_columns, list) or not all(isinstance(value, str) for value in required_columns) or not isinstance(min_rows, int) or min_rows < 0:
        raise ToolError("INVALID_ARGUMENTS", "Invalid required_columns or min_rows")
    missing = [column for column in required_columns if column not in result["columns"]]
    enough_rows = result["row_count"] >= min_rows
    return {"result_id": result["result_id"], "valid": not missing and enough_rows, "row_count": result["row_count"], "columns": result["columns"], "missing_columns": missing, "min_rows": min_rows, "enough_rows": enough_rows}


def _result_or_raise(state: dict[str, Any], result_id: str) -> dict[str, Any]:
    result = state.get("result_sets", {}).get(result_id)
    if not result:
        raise ToolError("RESULT_NOT_FOUND", f"Result {result_id} not found")
    return result


def _export_csv(path: Path, records: Iterable[dict[str, Any]], columns: list[str]) -> int:
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)
            count += 1
    return count


def _export_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


@tool(
    _schema(
        "export_result",
        "Creates a downloadable CSV or JSONL artifact for every matching row of a query result.",
        {"result_id": {"type": "string", "minLength": 1}, "format": {"type": "string", "enum": ["csv", "jsonl"], "default": "csv"}},
        ["result_id"],
    )
)
def export_result(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    state = ctx["state"]
    result = _result_or_raise(state, args.get("result_id", ""))
    output_format = args.get("format", "csv")
    if output_format not in {"csv", "jsonl"}:
        raise ToolError("INVALID_ARGUMENTS", "format must be csv or jsonl")
    table = _table(state, result["table_id"])
    columns, indices, filters = _prepare_query(table, result["query"])
    artifact_id = _id("art")
    relative_path = f"artifacts/{artifact_id}.{output_format}"
    output_path = session_file(state["session_id"], relative_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = _iter_matching_records(state, table, columns, indices, filters)
    count = _export_csv(output_path, records, columns) if output_format == "csv" else _export_jsonl(output_path, records)
    state.setdefault("artifacts", {})[artifact_id] = {"artifact_id": artifact_id, "path": relative_path, "format": output_format, "row_count": count, "file_name": f"extraction.{output_format}"}
    return {"artifact_id": artifact_id, "format": output_format, "row_count": count}


@tool(
    _schema(
        "submit_clarification",
        "Saves one or more questions for a user when the workbook/request is ambiguous.",
        {"questions": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "object"}}},
        ["questions"],
    )
)
def submit_clarification(ctx: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    questions = args.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 10:
        raise ToolError("INVALID_ARGUMENTS", "questions must contain 1 to 10 entries")
    sanitized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str) or not question["id"].strip() or not isinstance(question.get("question"), str) or not question["question"].strip():
            raise ToolError("INVALID_ARGUMENTS", "Every question needs non-empty id and question")
        if question["id"] in seen:
            raise ToolError("INVALID_ARGUMENTS", "Question ids must be unique")
        seen.add(question["id"])
        sanitized.append({key: _json_value(value) if key != "options" else [_json_value(item) for item in value] for key, value in question.items() if key in {"id", "question", "type", "options"}})
    token = _id("clr")
    state = ctx["state"]
    state.setdefault("clarifications", {})[token] = {"token": token, "status": "clarification_needed", "questions": sanitized, "answers": []}
    state["status"] = "clarification_needed"
    return {"token": token, "status": "clarification_needed", "questions": sanitized}
