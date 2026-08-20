"""Apply keyword operations onto a parsed SCHEDULE document."""

from __future__ import annotations

from typing import Any

from .keywords import KEYWORDS, normalize_keyword
from .parse import Block, Record, ScheduleDoc


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
                block.records = [r for r in block.records if not (r.tokens and r.tokens[0].strip("'") == well)]
        else:
            findings.append({"code": "OPERATION_UNKNOWN", "operation": action, "severity": "error"})
    return ScheduleDoc(blocks=blocks, text=doc.text, sha256=doc.sha256), findings
