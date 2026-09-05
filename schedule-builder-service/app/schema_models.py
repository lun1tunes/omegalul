"""Pydantic v2 models for the shared RAG ``schema_catalogue``.

One physical knowledge base; Schedule reads ``schema_catalogue`` (and optional
``details``) from ``schedule_mvp`` cards. Excel/orchestrator ignore those keys.
Do not add a Python subclass per keyword — variants come from the catalogue.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ParameterSpec(BaseModel):
    """One positional field of a SCHEDULE keyword record."""

    model_config = ConfigDict(extra="allow")

    name: str
    position: int
    type: str = "string"
    required: bool = False
    format: str | None = None
    quote: Literal["none", "single"] | None = None
    enum: list[str] | None = None
    default_allowed: bool = False
    case: Literal["upper"] | None = None
    unit: str | None = None
    description: str = ""
    true_token: str | None = None
    false_token: str | None = None

    @field_validator("name")
    @classmethod
    def _name_strip(cls, value: str) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("field name required")
        return name

    @field_validator("type")
    @classmethod
    def _type_norm(cls, value: str) -> str:
        kind = str(value or "string").strip().lower() or "string"
        return kind

    @field_validator("quote", mode="before")
    @classmethod
    def _quote_norm(cls, value: Any) -> str | None:
        text = str(value or "").strip().lower()
        if text in {"none", "single"}:
            return text
        return None

    @field_validator("enum", mode="before")
    @classmethod
    def _enum_str(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            return None
        return [str(item) for item in value]

    @field_validator("case", mode="before")
    @classmethod
    def _case_norm(cls, value: Any) -> str | None:
        text = str(value or "").strip().lower()
        return "upper" if text == "upper" else None


class LayoutSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    newline: str = "LF"
    indent: str = "  "
    delimiter: str = "SPACE"
    record_terminator: str = "SLASH"
    block_terminator: str = "SLASH_LINE"

    def newline_chars(self) -> str:
        return "\r\n" if str(self.newline).upper() == "CRLF" else "\n"

    def indent_text(self) -> str:
        return "    " if self.indent == "    " else "  "

    def delimiter_chars(self) -> str:
        return "\t" if str(self.delimiter).upper() == "TAB" else " "

    def record_suffix(self) -> str:
        return "" if str(self.record_terminator).upper() == "NONE" else " /"

    def block_slash_line(self) -> bool:
        return str(self.block_terminator).upper() != "NONE"


class CitationSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_id: str = ""
    document_revision: str = ""
    page: str = ""
    heading: str = ""
    source_hash: str = ""
    knowledge_id: str = ""
    revision: str = ""


class ParserSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    token_width: int = 0
    allow_trailing_omission: bool = True
    allow_unquoted_strings: bool = True


class KeywordSchema(BaseModel):
    """One catalogue entry: keyword + variant + ordered fields + layout."""

    model_config = ConfigDict(extra="allow")

    schema_id: str
    schema_revision: str
    keyword: str
    variant: str = "default"
    fields: list[ParameterSpec] = Field(default_factory=list)
    layout: LayoutSpec = Field(default_factory=LayoutSpec)
    semantics: dict[str, Any] = Field(default_factory=dict)
    citation: CitationSpec | None = None
    parser: ParserSpec = Field(default_factory=ParserSpec)

    @field_validator("keyword")
    @classmethod
    def _keyword_upper(cls, value: str) -> str:
        return str(value or "").strip().upper()

    @field_validator("variant")
    @classmethod
    def _variant_default(cls, value: str) -> str:
        return str(value or "").strip() or "default"

    def field_map(self) -> dict[str, ParameterSpec]:
        return {item.name.upper(): item for item in sorted(self.fields, key=lambda row: row.position)}

    def is_recordless(self) -> bool:
        layout = self.layout
        return (
            not self.fields
            and int(self.parser.token_width or 0) == 0
            and str(layout.record_terminator).upper() == "NONE"
            and str(layout.block_terminator).upper() == "NONE"
        )


class SimulatorProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vendor: str = "Rock Flow Dynamics"
    simulator: str = "tNavigator"
    version: str = "22.2"
    unit_system: str = "METRIC"


class SchemaCatalogue(BaseModel):
    """Approved machine-readable field layouts. Contract ``schedule_schema_catalogue`` / 1.0."""

    model_config = ConfigDict(extra="allow")

    contract: Literal["schedule_schema_catalogue"] = "schedule_schema_catalogue"
    contract_version: Literal["1.0"] = "1.0"
    catalogue_ref: str = ""
    catalogue_hash: str = ""
    source_hash: str = ""
    access_scope: str = "petroleum-engineering"
    simulator_profile: SimulatorProfile = Field(default_factory=SimulatorProfile)
    approved: bool = True
    approved_by: str = ""
    author: str = ""
    approval_gate_id: str = ""
    schemas: list[KeywordSchema] = Field(default_factory=list)

    def schema_map(self) -> dict[str, KeywordSchema]:
        out: dict[str, KeywordSchema] = {}
        for item in self.schemas:
            out[f"{item.keyword}::{item.variant}"] = item
        return out


class KeywordDetails(BaseModel):
    """Agent-facing nested object. Universal RAG key ``details`` with ``kind=schedule_keyword``."""

    kind: Literal["schedule_keyword"] = "schedule_keyword"
    keyword: str
    description: str = ""
    variants: list[dict[str, Any]] = Field(default_factory=list)
    source: Literal["schema_catalogue", "builtin"] = "schema_catalogue"


class IREvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str
    operation: str = "ADD"
    keyword: str
    variant: str = "default"
    fields: dict[str, Any] | list[Any] = Field(default_factory=dict)
    provenance: list[Any] = Field(default_factory=list)
    target_node_id: str = ""
    expected_raw_hash: str = ""
    file_ref: str = ""
    before_node_id: str = ""
    after_node_id: str = ""

    @field_validator("keyword")
    @classmethod
    def _kw(cls, value: str) -> str:
        return str(value or "").strip().upper()

    @field_validator("operation")
    @classmethod
    def _op(cls, value: str) -> str:
        return str(value or "ADD").strip().upper()

    @field_validator("variant")
    @classmethod
    def _var(cls, value: str) -> str:
        return str(value or "").strip() or "default"


class RenderRequest(BaseModel):
    mode: Literal["CREATE", "REVISE"] = "CREATE"
    schema_catalogue: dict[str, Any] | SchemaCatalogue
    ir_events: list[IREvent] | list[dict[str, Any]] = Field(default_factory=list)


def _listish(raw: Any) -> list[Any] | None:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, Mapping) and raw and all(str(key).isdigit() for key in raw):
        return [raw[key] for key in sorted(raw, key=lambda item: int(str(item)))]
    return None


def coerce_fields(schema: KeywordSchema, raw: Mapping[str, Any] | list[Any] | None) -> dict[str, Any]:
    listed = _listish(raw)
    if listed is not None:
        ordered = sorted(schema.fields, key=lambda row: row.position)
        return {item.name: listed[index] for index, item in enumerate(ordered) if index < len(listed)}
    if isinstance(raw, Mapping):
        out: dict[str, Any] = {}
        known = {item.name.upper(): item.name for item in schema.fields}
        for key, value in raw.items():
            name = known.get(str(key).strip().upper())
            if name:
                out[name] = value
            else:
                out[str(key)] = value
        return out
    return {}


def coerce_ir_events(raw: Any) -> list[Any]:
    payload = raw
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    listed = _listish(payload)
    if listed is not None:
        return [row for row in listed if isinstance(row, Mapping)]
    if isinstance(payload, Mapping) and (payload.get("keyword") or payload.get("event_id") or payload.get("operation")):
        return [payload]
    return []
