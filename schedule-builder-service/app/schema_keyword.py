"""Object wrapper around a catalogue keyword — not a hardcoded DATES/WCONPROD class."""

from __future__ import annotations

from typing import Any, Mapping

from .keywords import DESCRIPTIONS, FIELDS, normalize_keyword
from .schema_models import KeywordDetails, KeywordSchema, ParameterSpec, coerce_fields
from .schema_store import lookup, variants_for


def _overlay(keyword: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in FIELDS.get(keyword, []):
        if not isinstance(row, dict):
            continue
        raw = str(row.get("name") or "").strip()
        payload = {
            "unit": row.get("unit"),
            "description": str(row.get("description") or ""),
        }
        out[raw] = payload
        out[raw.upper()] = payload
    return out


def enrich_parameters(schema: KeywordSchema, keyword: str) -> list[ParameterSpec]:
    overlay = _overlay(keyword)
    rows: list[ParameterSpec] = []
    for field in sorted(schema.fields, key=lambda item: item.position):
        extra = overlay.get(field.name) or overlay.get(field.name.upper()) or {}
        data = field.model_dump()
        if not data.get("unit"):
            data["unit"] = extra.get("unit")
        if not data.get("description"):
            data["description"] = extra.get("description") or ""
        rows.append(ParameterSpec.model_validate(data))
    return rows


def specs_for(keyword: str) -> list[KeywordSchema]:
    code = normalize_keyword(keyword)
    return variants_for(code)


def details_for(keyword: str) -> KeywordDetails | None:
    code = normalize_keyword(keyword)
    schemas = specs_for(code)
    description = DESCRIPTIONS.get(code, f"SCHEDULE keyword {code}")
    if not schemas:
        builtin = FIELDS.get(code)
        if builtin is None and code not in DESCRIPTIONS:
            return None
        parameters = []
        for index, row in enumerate(builtin or [], start=1):
            parameters.append(
                {
                    "name": str(row.get("name") or "").upper() if str(row.get("name") or "").isupper() else str(row.get("name") or ""),
                    "position": index,
                    "type": row.get("type") or "string",
                    "required": bool(row.get("required")),
                    "unit": row.get("unit"),
                    "description": row.get("description") or "",
                }
            )
        return KeywordDetails(
            keyword=code,
            description=description,
            source="builtin",
            variants=[{"variant": "default", "parameters": parameters, "layout": {"newline": "LF", "indent": "  ", "delimiter": "SPACE", "record_terminator": "SLASH", "block_terminator": "SLASH_LINE"}}],
        )
    variants = []
    for schema in schemas:
        variants.append(
            {
                "variant": schema.variant,
                "schema_id": schema.schema_id,
                "schema_revision": schema.schema_revision,
                "parameters": [item.model_dump() for item in enrich_parameters(schema, code)],
                "layout": schema.layout.model_dump(),
            }
        )
    return KeywordDetails(keyword=code, description=description, source="schema_catalogue", variants=variants)


def resolve_schema(keyword: str, variant: str = "default", fields: Mapping[str, Any] | None = None) -> KeywordSchema | None:
    code = normalize_keyword(keyword)
    wanted = str(variant or "").strip() or "default"
    hit = lookup(code, wanted)
    if hit:
        return hit
    control = ""
    if isinstance(fields, Mapping):
        control = str(fields.get("CONTROL") or fields.get("control") or "").strip()
    if control:
        for item in variants_for(code):
            if item.variant.lower() == control.lower():
                return item
    found = variants_for(code)
    return found[0] if found else None


def fields_dict(schema: KeywordSchema, raw: Mapping[str, Any] | list[Any] | None) -> dict[str, Any]:
    return coerce_fields(schema, raw)
