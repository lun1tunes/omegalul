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


def _opaque_id(value: Any, prefix: str) -> str:
    """Validate and normalize an opaque ID returned by a previous tool.

    Tool-use models occasionally preserve an invisible trailing newline or space
    while copying IDs from a JSON response.  IDs are server-generated, never user
    supplied identifiers, so trimming surrounding whitespace is safe and avoids a
    needless failed/retried tool iteration.  The strict pattern still prevents an
    arbitrary key or path from being used for a state lookup.
    """
    if not isinstance(value, str):
        raise ToolError("INVALID_ARGUMENTS", f"{prefix}_id must be a string")
    normalized = value.strip()
    pattern = re.compile(rf"^{re.escape(prefix)}_[A-Za-z0-9_-]{{8,64}}$")
    if not pattern.fullmatch(normalized):
        raise ToolError("INVALID_ARGUMENTS", f"Invalid {prefix}_id")
    return normalized


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


def _row_bounds(row: list[Any]) -> tuple[int, int] | None:
    """Return the 1-based first/last meaningful cells in a row."""
    nonempty = [index for index, value in enumerate(row, start=1) if not _is_empty(value)]
    if not nonempty:
        return None
    return min(nonempty), max(nonempty)


def _filled_header_values(row: list[Any], start_column: int, end_column: int) -> list[Any]:
    """Fill merged-header blanks horizontally for an OpenAI-friendly display name.

    `openpyxl` deliberately exposes non-anchor cells of a merged range as `None` in
    read-only mode. For ``Q1`` merged over two sub-columns this produces
    ``["Q1", None]``. Carrying the anchor to the right gives useful deterministic
    names while still retaining the physical table coordinates.
    """
    result: list[Any] = []
    last_value: Any = None
    for index in range(start_column - 1, end_column):
        value = row[index] if index < len(row) else None
        if not _is_empty(value):
            last_value = value
        result.append(value if not _is_empty(value) else last_value)
    return result


def _join_header_rows(
    header_rows: list[list[Any]], start_column: int, end_column: int
) -> list[str]:
    """Combine one or more header rows into stable unique column names."""
    width = end_column - start_column + 1
    filled_rows = [_filled_header_values(row, start_column, end_column) for row in header_rows]
    raw_columns: list[str] = []
    for offset in range(width):
        parts: list[str] = []
        for row in filled_rows:
            value = row[offset]
            if _is_empty(value):
                continue
            text = str(value).strip()
            if not parts or parts[-1] != text:
                parts.append(text)
        raw_columns.append(" — ".join(parts) if parts else f"Column {get_column_letter(start_column + offset)}")

    used: Counter[str] = Counter()
    result: list[str] = []
    for value in raw_columns:
        used[value] += 1
        result.append(value if used[value] == 1 else f"{value} ({used[value]})")
    return result


def _header_score(row: list[Any], next_row: list[Any] | None) -> int:
    """Heuristic score for candidate headers in messy business worksheets.

    The score intentionally rewards text labels and a populated data-like next row,
    while penalising report metadata (two-cell key/value rows) and obvious total
    rows. It is only used to locate table regions; the agent can always inspect a
    preview and select a table explicitly.
    """
    bounds = _row_bounds(row)
    if not bounds:
        return -100
    start, end = bounds
    values = [row[index - 1] for index in range(start, end + 1)]
    populated = [value for value in values if not _is_empty(value)]
    text_count = sum(isinstance(value, str) and bool(value.strip()) for value in populated)
    nontext_count = len(populated) - text_count
    score = len(populated) * 2 + text_count * 2 - nontext_count * 2
    if len(populated) <= 2:
        score -= 3
    first = str(populated[0]).strip().casefold() if populated else ""
    if first in {"total", "subtotal", "итого", "всего"}:
        score -= 8
    if next_row is not None:
        next_bounds = _row_bounds(next_row)
        if next_bounds:
            overlap = max(0, min(end, next_bounds[1]) - max(start, next_bounds[0]) + 1)
            score += min(overlap, 8)
            next_values = [next_row[index - 1] if index - 1 < len(next_row) else None for index in range(start, end + 1)]
            if any(not _is_empty(value) and not isinstance(value, str) for value in next_values):
                score += 2
    return score


def _looks_like_data_row(row: list[Any], header: list[Any]) -> bool:
    """Whether a row is plausibly data for the preceding header.

    Header rows are overwhelmingly labels, whereas business records often contain a
    number/date/bool or an identifier such as ``INV-100`` / ``CC-20``. This keeps
    an ordinary all-text record from becoming a second header only when it exhibits
    a data-like signal; otherwise a second header remains possible.
    """
    header_bounds = _row_bounds(header)
    row_bounds = _row_bounds(row)
    if not header_bounds or not row_bounds:
        return False
    start = max(header_bounds[0], row_bounds[0])
    end = min(header_bounds[1], row_bounds[1])
    if start > end:
        return False
    values = [row[index - 1] if index - 1 < len(row) else None for index in range(start, end + 1)]
    nonempty = [value for value in values if not _is_empty(value)]
    if not nonempty:
        return False
    if any(isinstance(value, (int, float, bool, datetime, date, time)) for value in nonempty):
        return True
    # Identifier-shaped strings are much more common in records than in headings.
    if any(
        isinstance(value, str)
        and bool(re.search(r"(?:\d|[-_/])", value.strip()))
        for value in nonempty
    ):
        return True
    return False


def _is_likely_metadata_pair(values: list[Any]) -> bool:
    """Recognise common report key/value labels before opening a narrow table."""
    first = next((value for value in values if not _is_empty(value)), None)
    return isinstance(first, str) and first.strip().casefold() in {
        "report", "entity", "run date", "currency", "confidential", "prepared by",
        "generated", "source", "period", "version", "owner",
    }


def _split_row_segments(row: list[Any]) -> list[tuple[int, int]]:
    """Find contiguous non-empty column islands in one worksheet row."""
    segments: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(row, start=1):
        if not _is_empty(value):
            if start is None:
                start = index
        elif start is not None:
            segments.append((start, index - 1))
            start = None
    if start is not None:
        segments.append((start, len(row)))
    return segments


def _is_group_header_row(row: list[Any], next_row: list[Any] | None) -> bool:
    """Identify a sparse first row of a merged/multi-row header."""
    if next_row is None:
        return False
    bounds = _row_bounds(row)
    next_bounds = _row_bounds(next_row)
    if not bounds or not next_bounds:
        return False
    own_count = _nonempty_count(row)
    next_count = _nonempty_count(next_row)
    if own_count < 1 or next_count <= own_count:
        return False
    # A merged heading has a gap *inside its own populated span*.  Do not include
    # trailing cells needed only by the next row: ``Prepared by | Operations``
    # followed by a five-column header is report metadata, not a grouped header.
    # The slice is end-exclusive, hence ``bounds[1]`` itself must be included.
    return any(_is_empty(value) for value in row[bounds[0] - 1 : bounds[1]])


def _is_total_row(row: list[Any], start_column: int, end_column: int) -> bool:
    """Return whether a row begins with a conventional total/subtotal label."""
    values = row[start_column - 1 : end_column]
    first = next((value for value in values if not _is_empty(value)), None)
    if not isinstance(first, str):
        return False
    return first.strip().casefold() in {"total", "subtotal", "grand total", "итого", "всего"}


def _column_index(columns: list[str], requested: str) -> int:
    if requested in columns:
        return columns.index(requested)
    folded = requested.strip().casefold()
    matches = [index for index, column in enumerate(columns) if column.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    raise ToolError("COLUMN_NOT_FOUND", f"Column {requested} not found", {"available_columns": columns})


def _table(state: dict[str, Any], table_id: str | None) -> dict[str, Any]:
    """Return a detected table while keeping model-facing ID recovery bounded.

    ``detect_tables`` creates opaque IDs. A tool-using model can safely omit or
    slightly corrupt that opaque value only when this session has precisely one
    detected table; there is then no data-selection ambiguity. Multi-table
    workbooks continue to require an exact server-issued ID.
    """
    tables = state.get("tables", {})
    raw = table_id.strip() if isinstance(table_id, str) else ""
    if raw:
        try:
            normalized = _opaque_id(raw, "tbl")
        except ToolError:
            normalized = ""
        if normalized and normalized in tables:
            return tables[normalized]
    if len(tables) == 1:
        return next(iter(tables.values()))
    if not raw:
        message = "table_id is required when more than one table is detected"
    else:
        message = f"Table {raw} not found"
    raise ToolError("TABLE_NOT_FOUND", message, {"available_table_ids": list(tables)})


def _rows_for_table(state: dict[str, Any], table: dict[str, Any]) -> Iterator[list[Any]]:
    for row_number, row in _iter_sheet_rows(state, table["sheet"], table["data_start_row"], table["end_row"]):
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
        # Detection is deliberately streaming too: only the current and next rows
        # are held in memory. This matters for the supported 200 MB workbooks.
        # The one-row look-ahead is enough for grouped headers and for accepting a
        # single blank separator within an otherwise continuous data region.
        iterator = iter(_iter_sheet_rows(state, sheet))
        current_item = next(iterator, None)
        active: list[dict[str, Any]] = []
        while current_item is not None:
            row_number, row = current_item
            next_item = next(iterator, None)
            next_row = next_item[1] if next_item is not None else None
            bounds = _row_bounds(row)
            if bounds is None:
                next_active: list[dict[str, Any]] = []
                for current in active:
                    header = current["header_rows"][-1][1]
                    # Exported reports often insert a single blank visual
                    # separator in the middle of a table. Retain the table only
                    # when the immediately following row looks like its record.
                    if current["data_rows"] and next_row is not None and _looks_like_data_row(next_row, header):
                        next_active.append(current)
                    elif current["data_rows"]:
                        found.append(current)
                active = next_active
                if len(found) >= max_tables:
                    break
                current_item = next_item
                continue

            if not active:
                # Sparse merged group headers (e.g. Customer / FY2025 on row 1,
                # ID / Name / Revenue / Margin on row 2) are one table even
                # though the first row has multiple islands.
                if _is_group_header_row(row, next_row) and _nonempty_count(row) >= 2:
                    active.append(
                        {
                            "sheet": sheet,
                            "header_rows": [(row_number, row)],
                            "data_rows": [],
                            "start_column": min(bounds[0], _row_bounds(next_row)[0]),
                            "end_column": max(bounds[1], _row_bounds(next_row)[1]),
                        }
                    )
                    current_item = next_item
                    continue
                # Multiple horizontal islands on the same row are independent
                # tables (e.g. Sales in A:C and Stock in E:G), not one sparse
                # seven-column table.
                segments = _split_row_segments(row)
                # A row containing ordinary adjacent labels is one table. Split
                # only at a deliberate blank gap (A:C ... E:G), which is a common
                # dashboard layout for side-by-side tables.
                if len(segments) > 1:
                    candidate_segments = segments
                else:
                    candidate_segments = [(bounds[0], bounds[1])]
                for start_column, end_column in candidate_segments:
                    segment = row[start_column - 1 : end_column]
                    if len(segment) < 2 or _header_score(segment, None) < 4:
                        continue
                    # A two-cell key/value pair is more often preamble metadata
                    # than a table header. Keep genuine narrow tables when their
                    # immediate successor has a record signal (for example an ID
                    # or number), but wait for a wider header otherwise.
                    if len(segment) == 2:
                        if _is_likely_metadata_pair(segment):
                            continue
                        following_segment = (
                            next_row[start_column - 1 : end_column] if next_row is not None else []
                        )
                        if not _looks_like_data_row(following_segment, segment):
                            continue
                    active.append(
                        {
                            "sheet": sheet,
                            "header_rows": [(row_number, row)],
                            "data_rows": [],
                            "start_column": start_column,
                            "end_column": end_column,
                        }
                    )
                current_item = next_item
                continue

            next_active: list[dict[str, Any]] = []
            for current in active:
                header_rows: list[tuple[int, list[Any]]] = current["header_rows"]
                # A row belongs to the table only if it overlaps its header island.
                overlap = max(
                    0,
                    min(current["end_column"], bounds[1])
                    - max(current["start_column"], bounds[0])
                    + 1,
                )
                if overlap == 0:
                    if current["data_rows"]:
                        found.append(current)
                    continue

                if not current["data_rows"]:
                    previous_header = header_rows[-1][1]
                    # Combine rows only where the candidate has a genuine grouped
                    # header signal. A normal record directly below a header must
                    # start data, even if it happens to contain text in all cells.
                    previous_has_merged_gap = any(
                        _is_empty(value)
                        for value in previous_header[current["start_column"] - 1 : current["end_column"]]
                    )
                    candidate_has_merged_gap = any(
                        _is_empty(value)
                        for value in row[current["start_column"] - 1 : current["end_column"]]
                    )
                    if (
                        len(header_rows) < 2
                        and _nonempty_count(row) >= 2
                        and _header_score(row, next_row) >= 5
                        and (
                            _is_group_header_row(previous_header, row)
                            or (
                                not _looks_like_data_row(row, previous_header)
                                and (previous_has_merged_gap or candidate_has_merged_gap)
                            )
                        )
                    ):
                        header_rows.append((row_number, row))
                        next_active.append(current)
                        continue

                if current["data_rows"] and _is_total_row(row, current["start_column"], current["end_column"]):
                    found.append(current)
                    continue
                current["data_rows"].append((row_number, row))
                next_active.append(current)

            active = next_active
            if len(found) >= max_tables:
                break
            current_item = next_item

        for current in active:
            if current["data_rows"] and len(found) < max_tables:
                found.append(current)
        if len(found) >= max_tables:
            break

    response_tables: list[dict[str, Any]] = []
    for candidate in found:
        header_rows: list[tuple[int, list[Any]]] = candidate["header_rows"]
        data_rows: list[tuple[int, list[Any]]] = candidate["data_rows"]
        columns = _join_header_rows(
            [row for _, row in header_rows], candidate["start_column"], candidate["end_column"]
        )
        table_id = _id("tbl")
        persisted = {
            "table_id": table_id,
            "sheet": candidate["sheet"],
            "header_row": header_rows[0][0],
            "header_rows": [row_number for row_number, _ in header_rows],
            "data_start_row": data_rows[0][0],
            "start_column": candidate["start_column"],
            "end_column": candidate["end_column"],
            "end_row": data_rows[-1][0],
            "range": f"{get_column_letter(candidate['start_column'])}{header_rows[0][0]}:{get_column_letter(candidate['end_column'])}{data_rows[-1][0]}",
            "columns": columns,
        }
        state.setdefault("tables", {})[table_id] = persisted
        response_tables.append(
            {key: persisted[key] for key in ("table_id", "sheet", "header_row", "header_rows", "range")}
        )
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
        # A bounded, JSON-safe preview belongs in session state so the workflow
        # can return useful records without ever loading the complete result.
        # Full data remains in the session artifact/JSONL file.
        "preview_records": preview_rows,
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
    result_id = _opaque_id(args.get("result_id"), "res")
    result = state.get("result_sets", {}).get(result_id)
    if not result:
        raise ToolError("RESULT_NOT_FOUND", f"Result {result_id} not found")
    required_columns = args.get("required_columns", [])
    min_rows = args.get("min_rows", 1)
    if not isinstance(required_columns, list) or not all(isinstance(value, str) for value in required_columns) or not isinstance(min_rows, int) or min_rows < 0:
        raise ToolError("INVALID_ARGUMENTS", "Invalid required_columns or min_rows")
    missing = [column for column in required_columns if column not in result["columns"]]
    enough_rows = result["row_count"] >= min_rows
    return {"result_id": result["result_id"], "valid": not missing and enough_rows, "row_count": result["row_count"], "columns": result["columns"], "missing_columns": missing, "min_rows": min_rows, "enough_rows": enough_rows}


def _result_or_raise(state: dict[str, Any], result_id: str) -> dict[str, Any]:
    result_id = _opaque_id(result_id, "res")
    result = state.get("result_sets", {}).get(result_id)
    if not result:
        raise ToolError("RESULT_NOT_FOUND", f"Result {result_id} not found")
    return result


def _csv_value(value: Any) -> Any:
    """Prevent spreadsheet formula execution when an exported CSV is opened."""
    safe = _json_value(value)
    if isinstance(safe, str) and safe.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{safe}"
    return safe


def _export_csv(path: Path, records: Iterable[dict[str, Any]], columns: list[str]) -> int:
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writerow({column: _csv_value(column) for column in columns})
        for record in records:
            writer.writerow({column: _csv_value(record.get(column)) for column in columns})
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
