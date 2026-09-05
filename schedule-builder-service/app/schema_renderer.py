"""Catalogue-driven SCHEDULE IR renderer (Python port of n8n schedule_schema_runtime)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping

from .keywords import KEYWORDS, normalize_keyword, within_date_rank
from .schema_models import IREvent, KeywordSchema, ParameterSpec, SchemaCatalogue, coerce_fields, coerce_ir_events
from .schema_store import content_hash

_SHA = re.compile(r"^sha256:[a-f0-9]{64}$", re.I)
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
_ALLOWED_TYPES = {"string", "integer", "number", "date", "enum", "boolean"}


def _finding(code: str, severity: str = "error", **extra: Any) -> dict[str, Any]:
    row = {"code": code, "severity": severity}
    row.update(extra)
    return row


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _decimal(raw: Any) -> str | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(raw) if raw == raw and abs(raw) != float("inf") else None
    if isinstance(raw, str):
        text = raw.strip()
        return text if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text) else None
    return None


def _integer(raw: Any) -> str | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str):
        text = raw.strip()
        return text if re.fullmatch(r"[-+]?\d+", text) else None
    return None


def _render_date(raw: Any, fmt: str | None) -> str | None:
    text = str(raw or "").strip()
    parsed: date | None = None
    if isinstance(raw, datetime):
        parsed = raw.date()
    elif isinstance(raw, date):
        parsed = raw
    else:
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
        if match:
            year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            try:
                parsed = date(year, month, day)
            except ValueError:
                return None
    if parsed is None:
        return None
    if str(fmt or "").upper() == "DD MON YYYY":
        return f"{parsed.day} {_MONTHS[parsed.month - 1]} {parsed.year}"
    return parsed.isoformat()


def _default_token(raw: Any, field: ParameterSpec, findings: list[dict[str, Any]]) -> str | None:
    if not (isinstance(raw, Mapping) and str(raw.get("state") or "").lower() == "default"):
        return None
    if field.default_allowed is not True:
        findings.append(_finding("IR_DEFAULT_NOT_ALLOWED", field=field.name))
        return None
    count = int(raw.get("count") or 1)
    return f"{count}*" if count > 1 else "*"


def render_value(raw: Any, field: ParameterSpec, event: Mapping[str, Any], findings: list[dict[str, Any]]) -> str | None:
    token = _default_token(raw, field, findings)
    if token is not None:
        return token
    kind = str(field.type or "string").lower()
    out: str | None = None
    if kind == "integer":
        out = _integer(raw)
    elif kind == "number":
        out = _decimal(raw)
    elif kind == "date":
        out = _render_date(raw, field.format)
    elif kind == "boolean":
        if raw is True or str(raw).strip().lower() == "true":
            out = str(field.true_token or "YES")
        elif raw is False or str(raw).strip().lower() == "false":
            out = str(field.false_token or "NO")
    elif kind == "enum":
        values = [str(item) for item in (field.enum or [])]
        candidate = str(raw if raw is not None else "")
        hit = next((item for item in values if item.upper() == candidate.upper()), None)
        out = hit
    elif kind == "string":
        candidate = str(raw if raw is not None else "")
        if candidate and not re.search(r"[\r\n\0]", candidate):
            out = candidate
    if out is None:
        findings.append(
            _finding(
                "IR_FIELD_VALUE_INVALID",
                event_id=event.get("event_id"),
                keyword=event.get("keyword"),
                field=field.name,
                type=kind,
            )
        )
        return None
    if kind == "string" or field.quote == "single":
        return _quote(out)
    if field.case == "upper":
        return str(out).upper()
    return str(out)


def render_record_tokens(schema: KeywordSchema, values: Mapping[str, Any] | list[Any], *, event: Mapping[str, Any] | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    payload = event or {"event_id": "", "keyword": schema.keyword}
    fields = coerce_fields(schema, values)
    known = {item.name.upper() for item in schema.fields}
    unknown = [key for key in fields if str(key).upper() not in known]
    if unknown:
        findings.append(_finding("IR_UNKNOWN_FIELD", event_id=payload.get("event_id"), fields=unknown))
        return [], findings
    tokens: list[str] = []
    for field in sorted(schema.fields, key=lambda item: item.position):
        present = field.name in fields or field.name.upper() in {str(key).upper() for key in fields}
        raw = fields.get(field.name)
        if raw is None:
            for key, value in fields.items():
                if str(key).upper() == field.name.upper():
                    raw = value
                    present = True
                    break
        if not present:
            if field.required:
                findings.append(
                    _finding(
                        "IR_REQUIRED_FIELD_MISSING",
                        event_id=payload.get("event_id"),
                        keyword=schema.keyword,
                        field=field.name,
                    )
                )
                continue
            if field.default_allowed:
                tokens.append("*")
                continue
            findings.append(
                _finding(
                    "IR_OPTIONAL_FIELD_HAS_NO_DEFAULT_POLICY",
                    event_id=payload.get("event_id"),
                    keyword=schema.keyword,
                    field=field.name,
                )
            )
            continue
        token = render_value(raw, field, payload, findings)
        if token is None:
            continue
        tokens.append(token)
    return tokens, findings


def render_block_text(schema: KeywordSchema, tokens: list[str]) -> str:
    layout = schema.layout
    newline = layout.newline_chars()
    if schema.is_recordless() or not schema.fields:
        return f"{schema.keyword}{newline}"
    line = f"{schema.keyword}{newline}{layout.indent_text()}{layout.delimiter_chars().join(tokens)}{layout.record_suffix()}{newline}"
    if layout.block_slash_line():
        line += f"/{newline}{newline}"
    return line


def _validate_catalogue(catalogue: SchemaCatalogue, allowed: set[str]) -> tuple[dict[str, KeywordSchema], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    profile = catalogue.simulator_profile
    if catalogue.contract != "schedule_schema_catalogue" or catalogue.contract_version != "1.0":
        findings.append(_finding("SCHEMA_CATALOGUE_CONTRACT_INVALID"))
    if (
        str(profile.vendor) != "Rock Flow Dynamics"
        or str(profile.simulator).lower() != "tnavigator"
        or str(profile.version) != "22.2"
    ):
        findings.append(_finding("SCHEMA_PROFILE_NOT_APPROVED"))
    if not str(catalogue.approved_by or catalogue.author or "").strip():
        findings.append(_finding("SCHEMA_EXPERT_AUTHOR_REQUIRED"))
    if not _SHA.match(str(catalogue.source_hash or "").lower()):
        findings.append(_finding("SCHEMA_SOURCE_HASH_INVALID"))
    if not _SHA.match(str(catalogue.catalogue_hash or "").lower()):
        findings.append(_finding("SCHEMA_CATALOGUE_HASH_INVALID"))
    if not catalogue.schemas:
        findings.append(_finding("SCHEMA_CATALOGUE_EMPTY"))
    schema_map: dict[str, KeywordSchema] = {}
    for index, schema in enumerate(catalogue.schemas):
        keyword = normalize_keyword(schema.keyword)
        variant = schema.variant or "default"
        key = f"{keyword}::{variant}"
        if keyword not in allowed:
            findings.append(_finding("SCHEMA_KEYWORD_UNSUPPORTED", index=index, keyword=keyword))
            continue
        if not schema.schema_id or not schema.schema_revision:
            findings.append(_finding("SCHEMA_IDENTITY_REQUIRED", index=index, keyword=keyword))
            continue
        citation = schema.citation
        if citation is None:
            findings.append(_finding("SCHEMA_CITATION_MISSING", "warning", index=index, keyword=keyword))
        elif not (citation.document_id or citation.knowledge_id) or not (citation.document_revision or citation.revision):
            findings.append(_finding("SCHEMA_CITATION_INVALID", index=index, keyword=keyword))
            continue
        if not schema.semantics:
            findings.append(_finding("SCHEMA_SEMANTICS_REQUIRED", index=index, keyword=keyword, variant=variant))
            continue
        if key in schema_map:
            findings.append(_finding("SCHEMA_VARIANT_DUPLICATE", index=index, keyword=keyword, variant=variant))
            continue
        fields = sorted(schema.fields, key=lambda item: item.position)
        positions = [item.position for item in fields]
        names = [item.name for item in fields]
        if not schema.is_recordless() and (
            not fields
            or any(pos != index + 1 for index, pos in enumerate(positions))
            or any(not name for name in names)
            or len(set(names)) != len(names)
        ):
            findings.append(_finding("SCHEMA_FIELDS_INVALID", index=index, keyword=keyword))
            continue
        bad = next((item for item in fields if str(item.type).lower() not in _ALLOWED_TYPES), None)
        if bad:
            findings.append(_finding("SCHEMA_FIELD_TYPE_UNSUPPORTED", index=index, keyword=keyword, field=bad.name))
            continue
        schema_map[key] = schema.model_copy(update={"keyword": keyword, "variant": variant, "fields": fields})
    return schema_map, findings


def _control_token(schema: KeywordSchema, fields: Mapping[str, Any] | list[Any] | None) -> str:
    values = coerce_fields(schema, fields)
    for key in ("CONTROL", "control"):
        if key in values and values[key] not in (None, ""):
            return str(values[key]).strip()
    for key, value in values.items():
        if str(key).upper() == "CONTROL" and value not in (None, ""):
            return str(value).strip()
    return ""


def resolve_event_schema(
    schema_map: dict[str, KeywordSchema],
    keyword: str,
    variant: str,
    fields: Mapping[str, Any] | list[Any] | None,
) -> KeywordSchema | None:
    exact = schema_map.get(f"{keyword}::{variant}")
    candidates = [item for item in schema_map.values() if item.keyword == keyword]
    if not candidates:
        return None
    control = _control_token(exact or candidates[0], fields)
    if control:
        wanted = control.lower()
        for item in candidates:
            if item.variant.lower() == wanted:
                return item
    if exact:
        return exact
    if variant == "default" and len(candidates) == 1:
        return candidates[0]
    return None


def validate_and_render(
    *,
    mode: str = "CREATE",
    schema_catalogue: SchemaCatalogue | Mapping[str, Any],
    ir_events: list[IREvent] | list[Mapping[str, Any]],
    allowed_keywords: set[str] | None = None,
) -> dict[str, Any]:
    allowed = set(allowed_keywords or KEYWORDS)
    findings: list[dict[str, Any]] = []
    mode_u = str(mode or "CREATE").strip().upper()
    if mode_u not in {"CREATE", "REVISE"}:
        findings.append(_finding("RENDER_MODE_INVALID"))
    catalogue = (
        schema_catalogue
        if isinstance(schema_catalogue, SchemaCatalogue)
        else SchemaCatalogue.model_validate(schema_catalogue)
    )
    events: list[IREvent] = []
    for raw in coerce_ir_events(ir_events) if not isinstance(ir_events, list) else ir_events:
        events.append(raw if isinstance(raw, IREvent) else IREvent.model_validate(raw))
    needed = {normalize_keyword(item.keyword) for item in events}
    if needed:
        catalogue = catalogue.model_copy(
            update={"schemas": [item for item in catalogue.schemas if item.keyword in needed]}
        )
    schema_map, cat_findings = _validate_catalogue(catalogue, allowed)
    findings.extend(cat_findings)
    event_ids: set[str] = set()
    rendered_changes: list[dict[str, Any]] = []
    rendered_records: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_id = str(event.event_id or "").strip()
        if not event_id or event_id in event_ids:
            findings.append(_finding("IR_EVENT_ID_INVALID", index=index, event_id=event_id))
            continue
        event_ids.add(event_id)
        op = event.operation
        keyword = normalize_keyword(event.keyword)
        variant = event.variant
        if op not in {"KEEP", "MODIFY", "ADD", "REMOVE"}:
            findings.append(_finding("IR_OPERATION_INVALID", index=index, event_id=event_id))
            continue
        if keyword not in allowed:
            findings.append(_finding("IR_KEYWORD_UNSUPPORTED", index=index, event_id=event_id, keyword=keyword))
            continue
        if mode_u == "CREATE" and op != "ADD":
            findings.append(_finding("CREATE_REQUIRES_ADD_ONLY", index=index, event_id=event_id, operation=op))
            continue
        if mode_u == "REVISE" and op in {"MODIFY", "REMOVE"} and (
            not event.target_node_id or not _SHA.match(str(event.expected_raw_hash or "").lower())
        ):
            findings.append(_finding("IR_TARGET_IDENTITY_REQUIRED", index=index, event_id=event_id))
            continue
        base = {
            "event_id": event_id,
            "operation": op,
            "keyword": keyword,
            "variant": variant,
            "target_node_id": event.target_node_id or None,
            "expected_raw_hash": event.expected_raw_hash or None,
            "file_ref": event.file_ref or None,
            "before_node_id": event.before_node_id or None,
            "after_node_id": event.after_node_id or None,
            "provenance": list(event.provenance or [])[:100],
            "_ir_index": index,
        }
        if op in {"KEEP", "REMOVE"}:
            rendered_changes.append(base)
            continue
        schema = resolve_event_schema(schema_map, keyword, variant, event.fields)
        if schema is None:
            findings.append(
                _finding("IR_SCHEMA_VARIANT_NOT_FOUND", index=index, event_id=event_id, keyword=keyword, variant=variant)
            )
            continue
        variant = schema.variant
        base["variant"] = variant
        if not base["provenance"]:
            findings.append(_finding("IR_PROVENANCE_REQUIRED", index=index, event_id=event_id))
            continue
        tokens, field_findings = render_record_tokens(schema, event.fields, event=base)
        findings.extend(field_findings)
        if any(item.get("severity") == "error" for item in field_findings):
            continue
        text = render_block_text(schema, tokens)
        change = {
            **base,
            "rendered_text": text,
            "schema_id": schema.schema_id,
            "schema_revision": schema.schema_revision,
            "citation": schema.citation.model_dump() if schema.citation else None,
            "render_hash": content_hash(text),
        }
        rendered_changes.append(change)
        rendered_records.append(
            {
                "event_id": event_id,
                "keyword": keyword,
                "variant": variant,
                "field_count": len(tokens),
                "render_hash": change["render_hash"],
                "schema_id": schema.schema_id,
                "_ir_index": index,
            }
        )
    if mode_u == "CREATE" and not any(item.get("severity") == "error" for item in findings) and rendered_changes:
        segments: list[dict[str, Any]] = []
        current: dict[str, Any] = {"dates": None, "items": []}

        def flush() -> None:
            nonlocal current
            if current["dates"] or current["items"]:
                segments.append(current)
            current = {"dates": None, "items": []}

        for index, change in enumerate(rendered_changes):
            if change["keyword"] == "DATES":
                flush()
                current = {"dates": change, "items": []}
                continue
            current["items"].append({"change": change, "orig": change.get("_ir_index", index)})
        flush()
        ordered: list[dict[str, Any]] = []
        for segment in segments:
            if segment["dates"]:
                ordered.append(segment["dates"])
            items = list(segment["items"])
            items.sort(
                key=lambda row: (
                    within_date_rank(row["change"]["keyword"], row["orig"]),
                    row["orig"],
                )
            )
            ordered.extend(item["change"] for item in items)
        rendered_changes = ordered
        by_id = {row["event_id"]: row for row in rendered_records}
        rendered_records = [by_id[change["event_id"]] for change in rendered_changes if change["event_id"] in by_id]
    for row in rendered_changes:
        row.pop("_ir_index", None)
    for row in rendered_records:
        row.pop("_ir_index", None)
    hard = [item for item in findings if item.get("severity") == "error"]
    fingerprint_src = "\n".join(
        f"{item.schema_id}|{item.schema_revision}|{item.keyword}|{item.variant}"
        for item in sorted(catalogue.schemas, key=lambda item: f"{item.keyword}:{item.variant}")
    )
    return {
        "contract": "schedule_render_result",
        "contract_version": "1.0",
        "status": "needs_input" if hard else "rendered",
        "mode": mode_u,
        "changes": [] if hard else rendered_changes,
        "rendered_records": [] if hard else rendered_records,
        "catalogue_ref": catalogue.catalogue_ref or None,
        "catalogue_hash": str(catalogue.catalogue_hash or "").lower() or None,
        "catalogue_fingerprint": content_hash(fingerprint_src),
        "source_hash": str(catalogue.source_hash or "").lower() or None,
        "findings": findings,
        "hard_blockers": [item["code"] for item in hard],
        "metrics": {
            "ir_events": len(events),
            "schemas": len(catalogue.schemas),
            "rendered_records": 0 if hard else len(rendered_records),
            "passed": not hard,
        },
    }
