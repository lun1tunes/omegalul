from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("API_KEY", "test-key")
    # Reload because production settings are read at module import.
    import importlib
    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Заказы"
    sheet.append(["Заказ №", "Контрагент", "Сумма итого", "Статус"])
    sheet.append(["Z-1045", "ООО Ромашка", 15800.5, "Оплачен"])
    sheet.append(["Z-1046", "ООО Василек", 800, "Черновик"])
    sheet.append(["Z-1047", "ООО Ромашка", 1200, "Отгружен"])
    sheet.append([])
    sheet.append(["Справка", "не таблица"])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def upload(client: TestClient) -> str:
    response = client.post(
        "/api/v1/sessions",
        headers={"X-API-Key": "test-key"},
        files={"file": ("orders.xlsx", workbook_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"payload": '{"request":{"fields":["order_id"]}}'},
    )
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def tool(client: TestClient, session_id: str, name: str, args: dict) -> dict:
    response = client.post(f"/api/v1/sessions/{session_id}/tool", headers={"X-API-Key": "test-key"}, json={"name": name, "args": args})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"], payload
    return payload["result"]


def test_full_excel_tool_flow_and_artifact(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/api/v1/tools").status_code == 401
    schemas = client.get("/api/v1/tools", headers={"X-API-Key": "test-key"}).json()["tools"]
    names = {item["function"]["name"] for item in schemas}
    assert {"workbook_introspect", "sheet_preview", "detect_tables", "describe_table", "list_column_values", "query_table", "validate_result", "export_result", "submit_clarification", "resolve_clarification", "finalize_extraction"} <= names

    session_id = upload(client)
    meta = tool(client, session_id, "workbook_introspect", {})
    assert meta["sheets"][0]["name"] == "Заказы"
    preview = tool(client, session_id, "sheet_preview", {"sheet": "Заказы", "max_rows": 2})
    assert preview["rows"][1]["values"][0] == "Z-1045"
    detected = tool(client, session_id, "detect_tables", {"sheet": "Заказы"})
    table_id = detected["tables"][0]["table_id"]
    description = tool(client, session_id, "describe_table", {"table_id": table_id})
    assert description["columns"] == ["Заказ №", "Контрагент", "Сумма итого", "Статус"]
    statuses = tool(client, session_id, "list_column_values", {"table_id": table_id, "column": "статус"})
    assert statuses["distinct_count"] == 3
    queried = tool(client, session_id, "query_table", {"table_id": table_id, "select": ["Заказ №", "Контрагент", "Сумма итого"], "filters": [{"field": "Статус", "operator": "in", "value": ["Оплачен", "Отгружен"]}]})
    assert queried["row_count"] == 2
    assert queried["preview_rows"][0]["Заказ №"] == "Z-1045"
    validation = tool(client, session_id, "validate_result", {"result_id": queried["result_id"], "required_columns": ["Заказ №"]})
    assert validation["valid"] is True
    artifact = tool(client, session_id, "export_result", {"result_id": queried["result_id"], "format": "csv"})
    download = client.get(f"/api/v1/artifacts/{session_id}/{artifact['artifact_id']}", headers={"X-API-Key": "test-key"})
    assert download.status_code == 200
    assert "Z-1045" in download.content.decode("utf-8-sig")
    final = tool(client, session_id, "finalize_extraction", {"status": "success", "data": {"result_id": queried["result_id"], "row_count": 2, "returned_count": 2, "columns": queried["columns"], "records": queried["preview_rows"]}})
    assert final["output"]["status"] == "success"
    state = client.get(f"/api/v1/sessions/{session_id}/state", headers={"X-API-Key": "test-key"}).json()
    assert "file_path" not in state
    assert "path" not in state["artifacts"][artifact["artifact_id"]]


def test_structured_errors_batch_and_clarification(client: TestClient) -> None:
    session_id = upload(client)
    batch = client.post(
        f"/api/v1/sessions/{session_id}/tools/batch",
        headers={"X-API-Key": "test-key"},
        json={"calls": [{"call_id": "unknown", "name": "does_not_exist", "args": {}}, {"call_id": "introspect", "name": "workbook_introspect", "args": {}}]},
    )
    assert batch.status_code == 200
    results = batch.json()["results"]
    assert results[0]["ok"] is False and results[0]["error"]["code"] == "UNKNOWN_TOOL"
    assert results[1]["ok"] is True
    clarification = tool(client, session_id, "submit_clarification", {"questions": [{"id": "amount", "question": "Какую сумму использовать?", "type": "choice", "options": ["Сумма", "Сумма итого"]}]})
    resolved = tool(client, session_id, "resolve_clarification", {"token": clarification["token"], "answers": [{"question_id": "amount", "answer": "Сумма итого"}]})
    assert resolved["status"] == "resolved"


def test_rejects_pathlike_or_non_excel_upload(client: TestClient) -> None:
    response = client.post("/api/v1/sessions", headers={"X-API-Key": "test-key"}, files={"file": ("../../bad.txt", b"not excel", "text/plain")})
    assert response.status_code == 415
