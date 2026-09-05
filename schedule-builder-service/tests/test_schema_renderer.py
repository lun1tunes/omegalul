from __future__ import annotations

from app.keywords import keyword_object, normalize_keyword
from app.schema_renderer import validate_and_render
from app.schema_store import content_hash, load_catalogue, lookup


def _citation() -> dict:
    digest = content_hash("fixture")
    return {
        "document_id": "synthetic-test-catalogue",
        "document_revision": "22.2",
        "source_hash": digest,
        "page": "fixture",
        "heading": "TEST ONLY",
    }


def _catalogue() -> dict:
    digest = content_hash("fixture-catalogue")
    citation = _citation()
    return {
        "contract": "schedule_schema_catalogue",
        "contract_version": "1.0",
        "catalogue_ref": "catalogue://test/tnavigator/22.2",
        "catalogue_hash": digest,
        "source_hash": digest,
        "simulator_profile": {
            "vendor": "Rock Flow Dynamics",
            "simulator": "tNavigator",
            "version": "22.2",
        },
        "approved": True,
        "approved_by": "test-engineer",
        "approval_gate_id": "test-gate",
        "schemas": [
            {
                "schema_id": "fixture:DATES:v1",
                "schema_revision": "fixture-1",
                "keyword": "DATES",
                "variant": "default",
                "citation": citation,
                "fields": [
                    {
                        "name": "DATE",
                        "position": 1,
                        "type": "date",
                        "format": "DD MON YYYY",
                        "required": True,
                        "quote": "none",
                    }
                ],
                "semantics": {"period": "ANY", "clock": {"sets_from_field": "DATE"}},
                "layout": {
                    "newline": "LF",
                    "indent": "  ",
                    "delimiter": "SPACE",
                    "record_terminator": "SLASH",
                    "block_terminator": "SLASH_LINE",
                },
            },
            {
                "schema_id": "fixture:WCONPROD:v1",
                "schema_revision": "fixture-1",
                "keyword": "WCONPROD",
                "variant": "orat",
                "citation": citation,
                "fields": [
                    {"name": "WELL", "position": 1, "type": "string", "required": True, "quote": "single"},
                    {"name": "STATUS", "position": 2, "type": "enum", "enum": ["OPEN", "SHUT"], "required": True, "case": "upper"},
                    {"name": "CONTROL", "position": 3, "type": "enum", "enum": ["ORAT", "BHP"], "required": True, "case": "upper"},
                    {"name": "ORAT", "position": 4, "type": "number", "required": True},
                    {"name": "BHP", "position": 5, "type": "number", "required": False, "default_allowed": True},
                ],
                "semantics": {"period": "ANY", "clock": {"uses_current": True}},
                "layout": {
                    "newline": "LF",
                    "indent": "  ",
                    "delimiter": "SPACE",
                    "record_terminator": "SLASH",
                    "block_terminator": "SLASH_LINE",
                },
            },
        ],
    }


def test_render_dates_iso_to_eclipse() -> None:
    result = validate_and_render(
        mode="CREATE",
        schema_catalogue=_catalogue(),
        ir_events=[
            {
                "event_id": "e-dates",
                "operation": "ADD",
                "keyword": "DATES",
                "fields": {"DATE": "2025-01-01"},
                "provenance": [{"source": "test"}],
            }
        ],
    )
    assert result["status"] == "rendered", result["findings"]
    text = result["changes"][0]["rendered_text"]
    assert text.startswith("DATES\n")
    assert "1 JAN 2025 /" in text
    assert "\n/\n\n" in text


def test_render_wconprod_from_list_and_quoting() -> None:
    result = validate_and_render(
        mode="CREATE",
        schema_catalogue=_catalogue(),
        ir_events=[
            {
                "event_id": "e-wcon",
                "operation": "ADD",
                "keyword": "WCONPROD",
                "variant": "orat",
                "fields": ["P1", "open", "ORAT", 120.5],
                "provenance": [{"source": "test"}],
            }
        ],
    )
    assert result["status"] == "rendered", result["findings"]
    text = result["changes"][0]["rendered_text"]
    assert "WCONPROD" in text
    assert "'P1'" in text
    assert "OPEN" in text
    assert "120.5" in text
    assert text.rstrip().endswith("/") or "\n/\n" in text


def test_missing_required_field_is_hard_blocker() -> None:
    result = validate_and_render(
        mode="CREATE",
        schema_catalogue=_catalogue(),
        ir_events=[
            {
                "event_id": "e-bad",
                "operation": "ADD",
                "keyword": "DATES",
                "fields": {},
                "provenance": [{"source": "test"}],
            }
        ],
    )
    assert result["status"] == "needs_input"
    assert "IR_REQUIRED_FIELD_MISSING" in result["hard_blockers"]


def test_unknown_field_is_hard_blocker() -> None:
    result = validate_and_render(
        mode="CREATE",
        schema_catalogue=_catalogue(),
        ir_events=[
            {
                "event_id": "e-unknown",
                "operation": "ADD",
                "keyword": "DATES",
                "fields": {"DATE": "2025-01-01", "WELL": "P1"},
                "provenance": [{"source": "test"}],
            }
        ],
    )
    assert result["status"] == "needs_input"
    assert "IR_UNKNOWN_FIELD" in result["hard_blockers"]


def test_keyword_object_exposes_catalogue_details() -> None:
    item = keyword_object("DATES")
    assert item is not None
    assert item["details"]["kind"] == "schedule_keyword"
    names = [row["name"] for row in item["fields"]]
    assert "DATE" in names or "date" in names
    if item["source"] == "schema_catalogue":
        params = item["details"]["variants"][0]["parameters"]
        assert params[0]["position"] == 1
        assert params[0]["type"] == "date"


def test_corpus_dates_lookup_and_render() -> None:
    schema = lookup("DATES", "default")
    assert schema is not None
    result = validate_and_render(
        mode="CREATE",
        schema_catalogue=load_catalogue(),
        ir_events=[
            {
                "event_id": "e-corpus-dates",
                "operation": "ADD",
                "keyword": "DATES",
                "variant": "default",
                "fields": {"DATE": "2025-03-01"},
                "provenance": [{"source": "corpus"}],
            }
        ],
    )
    assert result["status"] == "rendered", result["findings"]
    text = result["changes"][0]["rendered_text"]
    assert "1 MAR 2025 /" in text
    assert "\n/\n\n" in text


def test_corpus_wconprod_resolves_orat_from_control() -> None:
    assert lookup("WCONPROD", "orat") is not None
    result = validate_and_render(
        mode="CREATE",
        schema_catalogue=load_catalogue(),
        ir_events=[
            {
                "event_id": "e-wcon-control",
                "operation": "ADD",
                "keyword": "WCONPROD",
                "fields": {"WELL": "P1", "STATUS": "OPEN", "CONTROL": "ORAT", "ORAT": 120},
                "provenance": [{"source": "corpus"}],
            }
        ],
    )
    assert result["status"] == "rendered", result["findings"]
    change = result["changes"][0]
    assert change["variant"] == "orat"
    text = change["rendered_text"]
    assert text.startswith("WCONPROD\n")
    assert "'P1'" in text
    assert "ORAT" in text
    assert "120" in text
    assert "\n/\n\n" in text


def test_fracture_alias_still_normalizes() -> None:
    assert normalize_keyword("FRACTURE_WELL") == "FRACTURE_SPECS"
    assert normalize_keyword("WELLTARG") == "WELTARG"
