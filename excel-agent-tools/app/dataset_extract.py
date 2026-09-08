"""Deterministic table → dataset conversion behind ``extract_table``.

The LLM names the table, the fields and (optionally) their source columns, types, filters, a key
field and an unpivot rule; everything here is plain Python over the detected table — no column is
guessed from words, no row is invented. Output rows are JSON-safe: dates ``YYYY-MM-DD``, numbers
``int``/``float``, text stripped, integer-like floats in text fields rendered ``"101"``.

    columns  {field: column} | [column, …] | omitted → every column, field name derived from the header
    types    {field: text|number|date|boolean}; missing → inferred from the values (≥ 80 % agreement)
    filters  [{field: <column or field>, operator, value}] on source values (query_table operators)
    key      dedupe rows by this field, first occurrence wins
    unpivot  {"columns": [wide columns] | "rest", "name_field": "date", "value_field": "rate"}
             — a wide table (one column per date / month) becomes long rows
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from mas_agent_kit import ToolError, parse_jsonish
from mas_agent_kit.dataset import FIELD_TYPES, dataset_name

from .excel_tools import _is_empty, _matches_filter

TYPE_AGREEMENT = 0.8
_NUMBER_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?$")
_TNAV_DATE_RE = re.compile(r"^\d{1,2}\s+['\"]?[A-Za-z]{3}['\"]?\s+\d{4}$")
_BOOL_WORDS = {"true": True, "false": False, "да": True, "нет": False, "yes": True, "no": False}
_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "j",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
        "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)
_EXCEL_EPOCH = datetime(1899, 12, 30)


# -- value normalisation ---------------------------------------------------------------------------


def norm_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("ё", "е"))


def as_text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value).lower()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def as_date(value: Any, *, allow_serial: bool = False) -> tuple[Any, bool]:
    """Normalise a cell to ``YYYY-MM-DD`` (or a tNavigator ``1 'JAN' 2026`` literal); (value, recognised).

    Excel serial numbers are accepted only when the field is *declared* a date (``allow_serial``): a
    column of plain numbers must never be inferred as dates.
    """
    if value in (None, ""):
        return None, False
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d"), True
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ].*)?", text):
        return text[:10], True
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat(), True
        except ValueError:
            return text, False
    m = re.fullmatch(r"(\d{1,2})[./]((?:19|20)\d{2})", text)  # month.year (wide headers) → first day of the month
    if m and isinstance(value, str):
        try:
            return date(int(m.group(2)), int(m.group(1)), 1).isoformat(), True
        except ValueError:
            return text, False
    if _TNAV_DATE_RE.match(text):
        return text, True
    if allow_serial and isinstance(value, (int, float)) and not isinstance(value, bool) and 20000 <= float(value) <= 80000:
        # Excel serial date that lost its number format (openpyxl gives a float): 1954…2119.
        return (_EXCEL_EPOCH + timedelta(days=float(value))).strftime("%Y-%m-%d"), True
    return text, False


def as_number(value: Any) -> tuple[Any, bool]:
    if isinstance(value, bool) or value in (None, ""):
        return None, False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float):
        return (int(value) if value.is_integer() else value), True
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if _NUMBER_RE.fullmatch(text):
        number = float(text.replace(",", "."))
        return (int(number) if number.is_integer() and "e" not in text.lower() else number), True
    return str(value).strip(), False


def as_boolean(value: Any) -> tuple[Any, bool]:
    if isinstance(value, bool):
        return value, True
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value), True
    word = str(value or "").strip().lower()
    if word in _BOOL_WORDS:
        return _BOOL_WORDS[word], True
    return str(value).strip(), False


def cast(value: Any, ftype: str) -> tuple[Any, bool]:
    """(normalised value, matched declared type). Empty cells are (None, True)."""
    if _is_empty(value):
        return None, True
    if ftype == "date":
        return as_date(value, allow_serial=True)
    if ftype == "number":
        return as_number(value)
    if ftype == "boolean":
        return as_boolean(value)
    return as_text(value), True


def infer_type(values: Iterable[Any]) -> str:
    """``boolean`` / ``date`` (≥ 80 % of non-empty values; a stray «после ГРП» does not break a date column) /
    ``number`` (every non-empty value numeric — one «N-7» makes an identifier column text) / ``text``."""
    sample = [v for v in values if not _is_empty(v)]
    if not sample:
        return "text"
    n = len(sample)
    if all(isinstance(v, bool) for v in sample):
        return "boolean"
    dates = sum(1 for v in sample if isinstance(v, (datetime, date)) or (isinstance(v, str) and as_date(v)[1]))
    if dates / n >= TYPE_AGREEMENT:
        return "date"
    if all(as_number(v)[1] for v in sample):
        return "number"
    return "text"


def field_name_from_header(header: str, used: set[str]) -> str:
    """A latin identifier for a header (``Дебит нефти`` → ``debit_nefti``); unique within the dataset."""
    text = norm_key(header).translate(_TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not text or not text[0].isalpha():
        text = "col_" + text if text else "col"
    base = text[:48]
    name, n = base, 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


# -- column resolution -----------------------------------------------------------------------------


def match_column(columns: list[str], wanted: Any) -> str | None:
    """Exact (case/space-insensitive) header match; a unique substring match as a fallback."""
    key = norm_key(wanted)
    if not key:
        return None
    for col in columns:
        if norm_key(col) == key:
            return str(col)
    hits = [str(col) for col in columns if key in norm_key(col)]
    return hits[0] if len(hits) == 1 else None


def resolve_columns(columns: list[str], spec: Any) -> tuple[dict[str, str], dict[str, str]]:
    """``columns`` argument → ({field: column}, {field: requested column not found}).

    Omitted / ``"*"`` → every column with a field name derived from its header.
    """
    raw = parse_jsonish(spec) if isinstance(spec, str) and spec.strip() not in {"", "*"} else spec
    if raw in (None, "", "*", [], {}):
        used: set[str] = set()
        return {field_name_from_header(col, used): col for col in columns}, {}
    mapping: dict[str, str] = {}
    missing: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, col_name in raw.items():
            fname = dataset_name(key)
            if not fname:
                raise ToolError("field_name_invalid", f"Имя поля «{key}» должно быть латинским идентификатором (например well, date, oil_rate).", {"field": str(key)})
            col = match_column(columns, col_name)
            if col:
                mapping[fname] = col
            else:
                missing[fname] = str(col_name)
        return mapping, missing
    if isinstance(raw, list):
        used = set()
        for col_name in raw:
            col = match_column(columns, col_name)
            if col:
                mapping[field_name_from_header(col, used)] = col
            else:
                missing[str(col_name)] = str(col_name)
        return mapping, missing
    raise ToolError("columns_invalid", "columns — JSON-объект {поле: колонка} или массив имён колонок.", {"got": type(raw).__name__})


def resolve_types(fields: Iterable[str], spec: Any) -> dict[str, str]:
    """``types`` argument → {field: type} for known fields and known types only."""
    known = set(fields)
    raw = parse_jsonish(spec) if isinstance(spec, str) else spec
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, ftype in raw.items():
        fname = dataset_name(key)
        t = str(ftype or "").strip().lower()
        if fname in known and t in FIELD_TYPES:
            out[fname] = t
    return out


def resolve_filters(columns: list[str], mapping: dict[str, str], spec: Any) -> list[tuple[str, dict[str, Any]]]:
    """Filters name a source column or a dataset field; returned as (column, condition)."""
    raw = parse_jsonish(spec) if isinstance(spec, str) else spec
    if isinstance(raw, dict):
        raw = [{"field": k, **(v if isinstance(v, dict) else {"operator": "eq", "value": v})} for k, v in raw.items()]
    out: list[tuple[str, dict[str, Any]]] = []
    for cond in raw if isinstance(raw, list) else []:
        if not isinstance(cond, dict):
            continue
        target = cond.get("field") or cond.get("column")
        col = match_column(columns, target) or mapping.get(dataset_name(target))
        if not col:
            raise ToolError("column_not_found", f"Фильтр ссылается на колонку «{target}», которой нет в таблице.", {"available_columns": columns})
        out.append((col, {"operator": str(cond.get("operator") or "eq"), "value": cond.get("value")}))
    return out


@dataclass
class Unpivot:
    columns: list[str]
    name_field: str
    value_field: str


def resolve_unpivot(columns: list[str], mapping: dict[str, str], spec: Any) -> Unpivot | None:
    raw = parse_jsonish(spec) if isinstance(spec, str) else spec
    if not isinstance(raw, dict) or not raw:
        return None
    name_field = dataset_name(raw.get("name_field") or "name")
    value_field = dataset_name(raw.get("value_field") or "value")
    if not name_field or not value_field or name_field == value_field:
        raise ToolError("unpivot_invalid", "unpivot.name_field и unpivot.value_field — разные латинские имена полей (например date и rate).")
    wanted = raw.get("columns")
    fixed = set(mapping.values())
    if wanted in (None, "", "rest", "*", []):
        wide = [c for c in columns if c not in fixed]
    else:
        wide = []
        missing = []
        for col_name in wanted if isinstance(wanted, list) else [wanted]:
            col = match_column(columns, col_name)
            (wide.append(col) if col else missing.append(str(col_name)))
        if missing:
            raise ToolError("column_not_found", "В unpivot.columns указаны колонки, которых нет в таблице.", {"not_found": missing, "available_columns": columns})
    if not wide:
        raise ToolError("unpivot_invalid", "Для unpivot не осталось «широких» колонок: все колонки уже заняты полями.", {"available_columns": columns})
    return Unpivot(columns=wide, name_field=name_field, value_field=value_field)


# -- rows ------------------------------------------------------------------------------------------


@dataclass
class Built:
    rows: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    issues: dict[str, Any] = field(default_factory=dict)


def build_dataset(
    records: Iterable[dict[str, Any]],
    mapping: dict[str, str],
    *,
    types: dict[str, str] | None = None,
    filters: list[tuple[str, dict[str, Any]]] | None = None,
    key_field: str = "",
    unpivot: Unpivot | None = None,
) -> Built:
    """Source records (header → cell) → typed dataset rows plus the field descriptors and counters."""
    all_records = list(records)
    source_rows: list[dict[str, Any]] = []
    filtered_out = 0
    for rec in all_records:
        if filters and not all(_matches_filter(rec.get(col), cond) for col, cond in filters):
            filtered_out += 1
            continue
        source_rows.append(rec)

    raw_rows: list[dict[str, Any]] = []
    for rec in source_rows:
        base = {fname: rec.get(col) for fname, col in mapping.items()}
        if unpivot is None:
            raw_rows.append(base)
            continue
        for col in unpivot.columns:
            cell = rec.get(col)
            if _is_empty(cell):
                continue
            raw_rows.append({**base, unpivot.name_field: col, unpivot.value_field: cell})

    field_names = list(mapping)
    if unpivot is not None:
        field_names += [unpivot.name_field, unpivot.value_field]
    declared = dict(types or {})
    if key_field:
        declared[key_field] = "text"  # identity; 101 and "N-7" must compare as the same kind of value
    mapped_samples = {fname: [rec.get(col) for rec in all_records] for fname, col in mapping.items()}
    if unpivot is not None:
        mapped_samples[unpivot.name_field] = list(unpivot.columns)
        mapped_samples[unpivot.value_field] = [rec.get(col) for rec in all_records for col in unpivot.columns]
    resolved_types: dict[str, str] = {}
    for fname in field_names:
        resolved_types[fname] = declared.get(fname) or infer_type(mapped_samples.get(fname) or (r.get(fname) for r in raw_rows))

    rows: list[dict[str, Any]] = []
    mismatches: dict[str, int] = {}
    skipped_empty = 0
    duplicates = 0
    seen: set[str] = set()
    for raw in raw_rows:
        out: dict[str, Any] = {}
        for fname in field_names:
            value, ok = cast(raw.get(fname), resolved_types[fname])
            if not ok:
                mismatches[fname] = mismatches.get(fname, 0) + 1
            if value is not None:
                out[fname] = value
        if not out:
            skipped_empty += 1
            continue
        if key_field:
            key = as_text(out.get(key_field)).casefold()
            if not key:
                skipped_empty += 1
                continue
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
        rows.append(out)

    fields = []
    for fname in field_names:
        descriptor: dict[str, Any] = {"name": fname, "type": resolved_types[fname]}
        if fname in mapping:
            descriptor["source_column"] = mapping[fname]
        elif unpivot is not None and fname == unpivot.name_field:
            descriptor["source_column"] = "заголовки колонок: " + ", ".join(unpivot.columns[:6]) + (" …" if len(unpivot.columns) > 6 else "")
        fields.append(descriptor)
    issues: dict[str, Any] = {}
    if mismatches:
        issues["type_mismatches"] = mismatches
    if skipped_empty:
        issues["skipped_empty"] = skipped_empty
    if duplicates:
        issues["duplicates"] = duplicates
    if filtered_out:
        issues["filtered_out"] = filtered_out
    return Built(rows=rows, fields=fields, issues=issues)
