"""Manifest-driven integration tests for awkward but common Excel layouts.

The corpus is synthetic and deterministic.  Tests deliberately assert that each target
is discoverable and usable rather than requiring detection to return no additional
candidates from report metadata.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "complex"
MANIFEST = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("API_KEY", "test-key")
    # Settings are deliberately read at import time by the production app.
    import importlib
    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def _tool(client: TestClient, session_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/sessions/{session_id}/tool",
        headers={"X-API-Key": "test-key"},
        json={"name": name, "args": args},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"], payload
    return payload["result"]


def _upload_fixture(client: TestClient, path: Path) -> str:
    response = client.post(
        "/api/v1/sessions",
        headers={"X-API-Key": "test-key"},
        files={
            "file": (
                path.name,
                path.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"payload": '{"request":{"purpose":"complex corpus integration test"}}'},
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def _find_expected_table(
    client: TestClient, session_id: str, tables: list[dict[str, Any]], expected: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for table in tables:
        if table["sheet"] != expected["sheet"] or table["header_rows"] != expected["header_rows"]:
            continue
        description = _tool(client, session_id, "describe_table", {"table_id": table["table_id"], "sample_rows": 10})
        if description["columns"] == expected["columns"] and description["row_count"] == expected["rows"]:
            matches.append((table, description))
    assert len(matches) == 1, {"expected": expected, "matches": matches, "detected": tables}
    return matches[0]


def test_complex_corpus_full_tool_flow(client: TestClient) -> None:
    """Every fixture supports introspect → detect → describe → query → validate → export."""
    for fixture in MANIFEST["fixtures"]:
        session_id = _upload_fixture(client, FIXTURE_ROOT / fixture["file"])
        metadata = _tool(client, session_id, "workbook_introspect", {})
        sheet_names = {item["name"] for item in metadata["sheets"]}
        assert {expected["sheet"] for expected in fixture["expected"]} <= sheet_names

        detected = _tool(client, session_id, "detect_tables", {"max_tables": 30})["tables"]
        for expected in fixture["expected"]:
            table, description = _find_expected_table(client, session_id, detected, expected)
            assert description["sample_rows"]

            queried = _tool(
                client,
                session_id,
                "query_table",
                {"table_id": table["table_id"], "select": expected["columns"], "limit": 100},
            )
            assert queried["row_count"] == expected["rows"]
            assert queried["columns"] == expected["columns"]

            validation = _tool(
                client,
                session_id,
                "validate_result",
                {"result_id": queried["result_id"], "required_columns": expected["columns"], "min_rows": expected["rows"]},
            )
            assert validation["valid"] is True

            exported = _tool(client, session_id, "export_result", {"result_id": queried["result_id"], "format": "csv"})
            assert exported["row_count"] == expected["rows"]


def test_complex_corpus_special_cases(client: TestClient) -> None:
    """Check semantics that cannot be expressed solely as table shape in the manifest."""
    unicode_id = _upload_fixture(client, FIXTURE_ROOT / "07_unicode_and_hidden_sheet.xlsx")
    metadata = _tool(client, unicode_id, "workbook_introspect", {})
    hidden = next(sheet for sheet in metadata["sheets"] if sheet["name"] == "Internal calculations")
    assert hidden["state"] == "hidden"

    dates_id = _upload_fixture(client, FIXTURE_ROOT / "10_dates_numbers_booleans.xlsx")
    tables = _tool(client, dates_id, "detect_tables", {"sheet": "Mixed types"})["tables"]
    expected = MANIFEST["fixtures"][-1]["expected"][0]
    table, _ = _find_expected_table(client, dates_id, tables, expected)
    queried = _tool(client, dates_id, "query_table", {"table_id": table["table_id"], "limit": 10})
    assert queried["preview_rows"][0]["Start date"] == "2024-06-01T00:00:00"
    assert queried["preview_rows"][0]["Active"] is True
    numeric_string_filter = _tool(
        client,
        dates_id,
        "query_table",
        {
            "table_id": table["table_id"],
            "select": ["Employee ID", "Base salary"],
            "filters": [{"field": "Base salary", "operator": "gt", "value": "60000"}],
        },
    )
    assert numeric_string_filter["row_count"] == 3

    duplicate_id = _upload_fixture(client, FIXTURE_ROOT / "09_duplicate_headers.xlsx")
    tables = _tool(client, duplicate_id, "detect_tables", {"sheet": "Duplicate columns"})["tables"]
    expected = MANIFEST["fixtures"][-2]["expected"][0]
    _, description = _find_expected_table(client, duplicate_id, tables, expected)
    assert "Sales (2)" in description["columns"]
