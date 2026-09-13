"""Apply keyword operations onto a parsed SCHEDULE document."""

from __future__ import annotations

from typing import Any

from .keywords import KEYWORDS, keyword_object, normalize_keyword
from .parse import Block, Record, ScheduleDoc, timeline_segments
from .well_model import is_factual_record


def parameter_specs(keyword: str) -> list[dict[str, Any]]:
    """Positional parameters of a keyword from the catalogue (or builtin FIELDS)."""
    item = keyword_object(keyword)
    if not item:
        return []
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    for variant in details.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        params = variant.get("parameters")
        if isinstance(params, list) and params:
            return [p for p in params if isinstance(p, dict)]
    return [p for p in (item.get("fields") or []) if isinstance(p, dict)]


def _field_get(fields: dict[str, Any], name: str) -> Any:
    if name in fields:
        return fields[name]
    low = name.lower()
    for key, value in fields.items():
        if str(key).lower() == low:
            return value
    return None


def format_param_token(param: dict[str, Any], value: Any) -> str:
    if value in (None, ""):
        return ""
    name = str(param.get("name") or "")
    quote = str(param.get("quote") or "").strip().lower()
    typ = str(param.get("type") or "").strip().lower()
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if typ in {"number", "integer", "float"}:
        try:
            num = float(str(value).replace(",", ".").replace(" ", ""))
            if num == int(num) and abs(num) < 1e15:
                return str(int(num))
            return f"{num:.10g}"
        except (TypeError, ValueError):
            pass
    if quote == "single" or (quote != "none" and name.upper() == "WELL"):
        return f"'{text}'"
    return text


def tokens_from_schema(keyword: str, fields: dict[str, Any]) -> list[str]:
    """Record tokens in catalogue parameter order. Empty optional params are omitted (trailing)."""
    params = sorted(parameter_specs(keyword), key=lambda p: int(p.get("position") or 0) or 0)
    tokens: list[str] = []
    if params:
        for param in params:
            raw = _field_get(fields, str(param.get("name") or ""))
            if raw in (None, ""):
                continue
            token = format_param_token(param, raw)
            if token:
                tokens.append(token)
        return tokens
    return _tokens_from_fields(fields)


def well_from_fields(fields: dict[str, Any], keyword: str = "") -> str:
    raw = _field_get(fields, "WELL")
    if raw in (None, ""):
        raw = _field_get(fields, "well")
    if raw in (None, "") and keyword:
        for param in parameter_specs(keyword):
            if str(param.get("name") or "").upper() == "WELL":
                raw = _field_get(fields, str(param.get("name") or ""))
                break
    return str(raw or "").strip().strip("'\"")


def _dates_equal(left: Any, right: Any) -> bool:
    return " ".join(str(left or "").split()).casefold() == " ".join(str(right or "").split()).casefold()


def _find_segment(doc: ScheduleDoc, date: str) -> dict[str, Any] | None:
    for segment in timeline_segments(doc):
        if _dates_equal(segment.get("date"), date):
            return segment
    return None


def apply_mapped_records(
    doc: ScheduleDoc,
    keyword: str,
    rows: list[dict[str, Any]],
    *,
    dates: list[str | None] | None = None,
) -> tuple[ScheduleDoc, list[dict[str, Any]]]:
    """Upsert records from schema-shaped field dicts.

    Updates every existing record of ``keyword`` whose WELL matches (optionally only on a DATES
    step). Adds a record when the well is absent. Does not invent DATES steps.
    """
    findings: list[dict[str, Any]] = []
    code = normalize_keyword(keyword)
    if code not in KEYWORDS:
        return doc, [{"code": "KEYWORD_UNSUPPORTED", "keyword": code, "severity": "error"}]
    blocks = list(doc.blocks)
    dated = dates if dates is not None and len(dates) == len(rows) else [None] * len(rows)

    def _snapshot() -> ScheduleDoc:
        return ScheduleDoc(blocks=blocks, text=doc.text, sha256=doc.sha256)

    def _touch(target_blocks: list[Block], fields: dict[str, Any]) -> int:
        well = well_from_fields(fields, code)
        tokens = tokens_from_schema(code, fields)
        updated = 0
        for block in target_blocks:
            if normalize_keyword(block.keyword) != code:
                continue
            for rec in block.records:
                rec_well = rec.tokens[0].strip("'\"") if rec.tokens else ""
                if well and rec_well == well:
                    rec.tokens = tokens
                    rec.raw = "  " + " ".join(tokens) + " /"
                    updated += 1
        return updated

    def _add(fields: dict[str, Any], date: str | None) -> None:
        tokens = tokens_from_schema(code, fields)
        record = Record(tokens=tokens, raw="  " + " ".join(tokens) + " /")
        new_block = Block(keyword=code, records=[record], raw_body="", known=True)
        if date:
            segment = _find_segment(_snapshot(), date)
            scope = list((segment or {}).get("blocks") or [])
            target = next((b for b in reversed(scope) if normalize_keyword(b.keyword) == code), None)
            if target is not None:
                target.records.append(record)
                return
            dates_block = (segment or {}).get("dates_block")
            if dates_block is None:
                blocks.append(new_block)
                return
            try:
                anchor = blocks.index(dates_block)
            except ValueError:
                blocks.append(new_block)
                return
            insert_at = anchor + 1
            while insert_at < len(blocks) and normalize_keyword(blocks[insert_at].keyword) != "DATES":
                insert_at += 1
            blocks.insert(insert_at, new_block)
            return
        target = next((b for b in reversed(blocks) if normalize_keyword(b.keyword) == code), None)
        if target is None:
            blocks.append(new_block)
        else:
            target.records.append(record)

    for fields, date in zip(rows, dated):
        if not isinstance(fields, dict):
            continue
        if date:
            segment = _find_segment(_snapshot(), date)
            if segment is None:
                findings.append({"code": "DATE_NOT_IN_SCHEDULE", "date": date, "severity": "error"})
                continue
            target_blocks = list(segment.get("blocks") or [])
        else:
            target_blocks = blocks
        if _touch(target_blocks, fields) == 0:
            _add(fields, date)
    return ScheduleDoc(blocks=blocks, text=doc.text, sha256=doc.sha256), findings


def _tokens_from_fields(fields: dict[str, Any]) -> list[str]:
    order = ["well", "status", "ORAT", "WRAT", "GRAT", "LRAT", "RESV", "BHP", "THP", "date", "group", "i", "j"]
    tokens: list[str] = []
    seen = set()
    for key in order:
        if key in fields and fields[key] not in (None, ""):
            value = fields[key]
            token = f"'{value}'" if key in {"well", "status", "group"} and not str(value).startswith("'") else str(value)
            tokens.append(token)
            seen.add(key)
    for key, value in fields.items():
        if key in seen or value in (None, ""):
            continue
        tokens.append(str(value))
    return tokens


def apply_operations(doc: ScheduleDoc, operations: list[dict[str, Any]]) -> tuple[ScheduleDoc, list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    blocks = list(doc.blocks)
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        action = str(op.get("operation") or op.get("op") or "ADD").upper()
        keyword = normalize_keyword(str(op.get("keyword") or ""))
        if keyword not in KEYWORDS:
            findings.append({"code": "KEYWORD_UNSUPPORTED", "keyword": keyword, "severity": "error"})
            continue
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        well = str(fields.get("well") or op.get("well") or "").strip().strip("'")
        if action == "ADD":
            tokens = _tokens_from_fields(fields) if fields else [str(t) for t in (op.get("tokens") or [])]
            record = Record(tokens=tokens, raw="  " + " ".join(tokens) + " /")
            target = next((b for b in reversed(blocks) if b.keyword == keyword), None)
            if target is None:
                blocks.append(Block(keyword=keyword, records=[record], raw_body="", known=True))
            else:
                target.records.append(record)
        elif action in {"MODIFY", "UPDATE"}:
            target = next((b for b in reversed(blocks) if b.keyword == keyword), None)
            if target is None:
                findings.append({"code": "KEYWORD_BLOCK_MISSING", "keyword": keyword, "severity": "error"})
                continue
            updated = False
            for rec in target.records:
                rec_well = rec.tokens[0].strip("'") if rec.tokens else ""
                if well and rec_well == well:
                    rec.tokens = _tokens_from_fields(fields) or rec.tokens
                    rec.raw = "  " + " ".join(rec.tokens) + " /"
                    updated = True
                    break
            if not updated:
                findings.append({"code": "WELL_NOT_FOUND", "keyword": keyword, "well": well, "severity": "error"})
        elif action == "REMOVE":
            for block in blocks:
                if block.keyword != keyword:
                    continue
                kept: list[Record] = []
                for record in block.records:
                    matches = record.tokens and record.tokens[0].strip("'\"") == well
                    if matches and keyword == "WCONPROD" and is_factual_record(record):
                        findings.append(
                            {
                                "code": "FACTUAL_WCONPROD_PROTECTED",
                                "keyword": keyword,
                                "well": well,
                                "severity": "error",
                                "message": "Factual WCONPROD cannot be deleted.",
                            }
                        )
                        kept.append(record)
                    elif not matches:
                        kept.append(record)
                block.records = kept
        else:
            findings.append({"code": "OPERATION_UNKNOWN", "operation": action, "severity": "error"})
    return ScheduleDoc(blocks=blocks, text=doc.text, sha256=doc.sha256), findings
