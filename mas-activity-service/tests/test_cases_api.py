"""Unit tests for Decision/AgentTask/AgentResult contracts and /cases API."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.pop("CONTROL_PLANE_PROXY_URL", None)
os.environ["CONTROL_PLANE_REQUIRED"] = "false"

from fastapi.testclient import TestClient
import pytest

from app.contracts import AgentResult, AgentTask, Decision, compact_decision_context, empty_state
from app.main import app, reset_store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch) -> None:
    for key in (
        "CONTROL_PLANE_PROXY_URL",
        "CONTROL_PLANE_REQUIRED",
        "ORCHESTRATOR_WEBHOOK_URL",
        "N8N_BASE_URL",
        "ACTIVITY_STATE_PATH",
        "ACTIVITY_BINARIES_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    reset_store()


client = TestClient(app)


def test_ensure_schema_is_noop_without_database() -> None:
    from app import control_plane

    report = control_plane.ensure_schema()
    assert report["ok"] is True
    assert report["backend"] == "memory"
    sql = control_plane.control_plane_sql()
    assert "CREATE TABLE IF NOT EXISTS cases" in sql
    assert "agent_id TEXT" in sql
    assert "schedule_builder" in sql
    assert "DROP TABLE" not in sql
    stmts = control_plane.sql_statements(sql)
    assert stmts[0].startswith("CREATE TABLE IF NOT EXISTS cases")
    assert any(s.startswith("INSERT INTO agent_registry") and "schedule_builder" in s for s in stmts)
    leaked = control_plane.sql_statements(
        "-- volume; lab\nINSERT INTO t (x) VALUES ('a; b');\nCREATE TABLE IF NOT EXISTS y (id TEXT);\n"
    )
    assert leaked == [
        "INSERT INTO t (x) VALUES ('a; b')",
        "CREATE TABLE IF NOT EXISTS y (id TEXT)",
    ]
    init = "\n\n".join(
        [
            (ROOT.parent / "postgres-init" / "02-mas-control-plane.sql").read_text(encoding="utf-8"),
            (ROOT.parent / "postgres-init" / "03-schedule-builder-registry.sql").read_text(encoding="utf-8"),
        ]
    )
    assert control_plane.sql_statements(sql) == control_plane.sql_statements(init)


def test_empty_state_is_compact() -> None:
    state = empty_state("CASE-1", "goal")
    assert state["case_id"] == "CASE-1"
    assert state["status"] == "new"
    assert state["data"] == {}
    ctx = compact_decision_context(state)
    assert ctx["goal"] == "goal"
    assert ctx["task_name"] == ""
    assert ctx["artifacts_present"] == []
    assert ctx["hitl_pending"] is False


def test_decision_call_agent_roundtrip() -> None:
    decision = Decision.model_validate(
        {
            "status_message": "Сбор данных",
            "action": {
                "type": "call_agent",
                "agent_id": "excel_extractor",
                "task_id": "TASK-1",
                "handoff_message": "Достань даты",
                "task": {"excel_artifact": "a.xlsx"},
            },
        }
    )
    assert decision.action.type == "call_agent"
    assert decision.action.agent_id == "excel_extractor"


def test_agent_task_result_schemas() -> None:
    task = AgentTask(
        case_id="CASE-1",
        task_id="TASK-1",
        agent_id="excel_extractor",
        objective="extract",
    )
    result = AgentResult(task_id="TASK-1", status="completed", message="ok", data={"rows": 2})
    assert task.case_id == "CASE-1"
    assert result.status == "completed"
    assert (ROOT / "schemas/decision.schema.json").is_file()


def test_create_case_without_n8n_is_503() -> None:
    res = client.post(
        "/cases",
        data={"task_description": "Извлечь скважины", "requested_by": "tester"},
    )
    assert res.status_code == 503
    listed = client.get("/cases").json()["cases"]
    assert listed == []


def test_create_case_memory_when_webhook_configured(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post(
        "/cases",
        data={"task_description": "Извлечь скважины из Excel", "requested_by": "tester"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    case_id = body["case_id"]
    state = client.get(f"/cases/{case_id}/state")
    assert state.status_code == 200
    assert state.json()["state"]["goal"].startswith("Извлечь")
    events = client.get(f"/cases/{case_id}/events")
    kinds = [e["kind"] for e in events.json()["events"]]
    assert "case.created" in kinds
    listed = client.get("/cases")
    assert any(item["case_id"] == case_id for item in listed.json()["cases"])
    feed = client.get(f"/cases/{case_id}")
    assert feed.status_code == 200
    schema = feed.json()["schema"]
    assert schema["start_label"] == "Постановка задачи"
    assert schema["end_label"] == "Результат"
    assert schema["frames"][0]["label"] == "Постановка задачи"
    assert schema["frames"][0]["nodes"]["input"]["tone"] == "active"


def test_create_action_is_sent_to_n8n_for_new_case(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://n8n.test/webhook/mas-orchestrator-step")
    from app.settings import Settings

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    captured: dict[str, object] = {}

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr("app.cases_api.invoke_orchestrator", fake_invoke)
    res = client.post(
        "/cases",
        data={
            "task_description": "Создать новую задачу",
            "task_name": "Новая задача",
            "requested_by": "tester",
        },
    )

    assert res.status_code == 200, res.text
    payload = captured["payload"]
    assert payload["action"] == "create"
    assert payload["case_id"] == res.json()["case_id"]
    assert payload["task_description"] == "Создать новую задачу"
    assert payload["task_name"] == "Новая задача"


def test_create_keeps_files_in_activity_not_n8n(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://n8n.test/webhook/mas-orchestrator-step")
    from app.settings import Settings
    from app import control_plane

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    captured: dict[str, object] = {}

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        captured["payload"] = payload
        captured["files"] = files
        return {"ok": True}

    monkeypatch.setattr("app.cases_api.invoke_orchestrator", fake_invoke)
    res = client.post(
        "/cases",
        data={"task_description": "Создать задачу с файлами", "requested_by": "tester"},
        files=[
            ("file", ("dates.xlsx", b"xlsx-bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("schedule_files", ("base.inc", b"DATES\n/", "text/plain")),
        ],
    )

    assert res.status_code == 200, res.text
    assert captured["files"] is None
    payload = captured["payload"]
    assert payload["action"] == "create"
    case_id = res.json()["case_id"]
    row = control_plane.get_case(case_id)
    artifacts = (row or {}).get("state", {}).get("artifacts") or {}
    assert artifacts["excel"]["artifact_id"] == "excel"
    assert artifacts["excel"]["filename"] == "dates.xlsx"
    assert artifacts["schedule_source"]["artifact_id"] == "schedule_source"
    inc = client.get(f"/cases/{case_id}/artifacts/schedule_source")
    assert inc.status_code == 200
    assert inc.content == b"DATES\n/"
    xlsx = client.get(f"/cases/{case_id}/artifacts/excel")
    assert xlsx.status_code == 200
    assert xlsx.content == b"xlsx-bytes"


def test_create_uses_artifact_proxy_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://n8n.test/webhook/mas-orchestrator-step")
    from app import artifact_store

    monkeypatch.setattr(artifact_store, "configured", lambda: True)
    captured: list[tuple[str, str, str, bytes, str]] = []

    async def fake_put(case_id, artifact_id, filename, content, mime_type):
        captured.append((case_id, artifact_id, filename, content, mime_type))
        return {"ok": True, "artifact_id": artifact_id}

    monkeypatch.setattr(artifact_store, "put", fake_put)
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    monkeypatch.setattr("app.cases_api.save_task_binaries", lambda *_args: pytest.fail("local artifact store used"))

    res = client.post(
        "/cases",
        data={"task_description": "Proxy upload", "requested_by": "tester"},
        files=[("file", ("dates.xlsx", b"xlsx-bytes", "application/octet-stream"))],
    )

    assert res.status_code == 200, res.text
    assert captured == [
        (res.json()["case_id"], "excel", "dates.xlsx", b"xlsx-bytes", "application/octet-stream")
    ]


def test_control_plane_proxy_carries_hitl_and_errors(monkeypatch) -> None:
    from app import control_plane

    calls: list[tuple[str, dict[str, object]]] = []

    def fake_proxy(operation, **payload):
        calls.append((operation, payload))
        if operation == "get_case":
            return {"case_id": "CASE-1", "state": {}, "status": "running"}
        if operation == "list_events":
            return []
        if operation == "list_errors":
            return [{"error_id": 1, "case_id": "CASE-1"}]
        return {"event_id": 1, "idempotent": False}

    monkeypatch.setattr(control_plane, "_configured", lambda: True)
    monkeypatch.setattr(control_plane, "proxy_call", fake_proxy)

    control_plane.append_event(
        "CASE-1",
        kind="hitl.answered",
        actor="user",
        status="running",
        payload={"question_id": "Q-1", "answer": "ok"},
    )
    control_plane.list_events("CASE-1", after_seq=4)
    control_plane.append_error_trace(
        case_id="CASE-1",
        execution_id="exec-1",
        workflow_name="Orchestrator",
        node_name="Decision",
        error_message="boom",
        error_type="RuntimeError",
        stack="trace",
        input_snapshot={"x": 1},
    )
    assert control_plane.list_errors("CASE-1") == [{"error_id": 1, "case_id": "CASE-1"}]
    assert [name for name, _payload in calls] == [
        "append_event",
        "list_events",
        "append_error",
        "list_errors",
    ]
    assert calls[0][1]["kind"] == "hitl.answered"
    assert calls[2][1]["error_message"] == "boom"


def test_create_case_optional_task_name(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    named = client.post(
        "/cases",
        data={
            "task_description": "Сдвинуть даты ввода",
            "task_name": "  Даты ввода 4кв  ",
            "requested_by": "tester",
        },
    )
    assert named.status_code == 200, named.text
    body = named.json()
    assert body["task_name"] == "Даты ввода 4кв"
    case_id = body["case_id"]
    state = client.get(f"/cases/{case_id}/state").json()["state"]
    assert state["task_name"] == "Даты ввода 4кв"
    feed = client.get(f"/cases/{case_id}").json()
    assert feed["task_name"] == "Даты ввода 4кв"
    listed = client.get("/cases").json()["cases"]
    row = next(item for item in listed if item["case_id"] == case_id)
    assert row["task_name"] == "Даты ввода 4кв"
    assert row["title"] == "Даты ввода 4кв"

    unnamed = client.post(
        "/cases",
        data={"task_description": "Без названия", "requested_by": "tester"},
    )
    assert unnamed.status_code == 200, unnamed.text
    other = unnamed.json()["case_id"]
    bare = client.get(f"/cases/{other}").json()
    assert bare["task_name"] == ""
    listed = client.get("/cases").json()["cases"]
    row = next(item for item in listed if item["case_id"] == other)
    assert row["task_name"] == ""
    assert row["task_id"] == other


def test_patch_case_renames_existing_task(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post("/cases", data={"task_description": "Старое имя", "requested_by": "tester"})
    assert res.status_code == 200, res.text
    case_id = res.json()["case_id"]
    assert res.json()["task_name"] == ""
    patched = client.patch(f"/cases/{case_id}", json={"task_name": "  DKS NORTH1  "})
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["ok"] is True
    assert body["task_name"] == "DKS NORTH1"
    assert body["title"] == "DKS NORTH1"
    feed = client.get(f"/cases/{case_id}").json()
    assert feed["task_name"] == "DKS NORTH1"
    listed = client.get("/cases").json()["cases"]
    row = next(item for item in listed if item["case_id"] == case_id)
    assert row["task_name"] == "DKS NORTH1"
    cleared = client.patch(f"/cases/{case_id}", json={"task_name": "   "})
    assert cleared.status_code == 200
    assert cleared.json()["task_name"] == ""
    missing = client.patch("/cases/CASE-nope", json={"task_name": "x"})
    assert missing.status_code == 404


def test_case_upload_roundtrip_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    monkeypatch.setenv("ACTIVITY_BINARIES_PATH", str(tmp_path / "bins"))
    from app.settings import Settings

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post(
        "/cases",
        data={"task_description": "INC + Excel", "requested_by": "tester"},
        files=[
            ("file", ("wells.xlsx", b"PK\x03\x04fake", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("schedule_files", ("base.inc", b"DATES\n  1 JAN 2020 /\n/\n", "application/octet-stream")),
        ],
    )
    assert res.status_code == 200, res.text
    case_id = res.json()["case_id"]
    inc = client.get(f"/cases/{case_id}/artifacts/schedule_source")
    assert inc.status_code == 200
    assert b"DATES" in inc.content
    xlsx = client.get(f"/cases/{case_id}/artifacts/excel")
    assert xlsx.status_code == 200
    assert xlsx.content.startswith(b"PK")


def test_hitl_answer_and_agent_event(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings
    from app import control_plane

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post("/cases", data={"task_description": "HITL", "requested_by": "tester"})
    case_id = res.json()["case_id"]
    control_plane.update_case(
        case_id,
        state={
            **control_plane.get_case(case_id)["state"],
            "hitl": {"pending": True, "questions": [{"question_id": "Q-1", "question": "?"}], "answers": {}},
        },
        status="waiting_user",
    )
    posted = client.post(
        f"/cases/{case_id}/events",
        json={
            "kind": "agent.accepted",
            "actor": "excel_extractor",
            "agent_id": "excel_extractor",
            "status_message": "Разбираю Excel",
        },
    )
    assert posted.status_code == 200
    ans = client.post(f"/cases/{case_id}/answer", json={"question_id": "Q-1", "answer": "DD.MM.YYYY"})
    assert ans.status_code == 200
    kinds = [e["kind"] for e in client.get(f"/cases/{case_id}/events").json()["events"]]
    assert "hitl.answered" in kinds
    assert "agent.accepted" in kinds
    errors = client.get(f"/cases/{case_id}/errors")
    assert errors.status_code == 200
    assert errors.json()["errors"] == []


def test_hitl_multipart_attaches_schedule(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    monkeypatch.setenv("ACTIVITY_BINARIES_PATH", str(tmp_path / "bins"))
    from app.settings import Settings
    from app import control_plane

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post(
        "/cases",
        data={"task_description": "HITL files", "requested_by": "tester"},
        files=[("file", ("wells.xlsx", b"PK\x03\x04old", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    )
    assert res.status_code == 200, res.text
    case_id = res.json()["case_id"]
    control_plane.update_case(
        case_id,
        state={
            **control_plane.get_case(case_id)["state"],
            "hitl": {"pending": True, "questions": [{"question_id": "Q-inc", "question": "INC?"}], "answers": {}},
        },
        status="waiting_user",
    )
    ans = client.post(
        f"/cases/{case_id}/answer",
        data={
            "question_id": "Q-inc",
            "answer": "вот недостающий INCLUDE",
            "requested_by": "tester",
        },
        files=[("schedule_files", ("WELLS.INC", b"WELSPECS\n/", "text/plain"))],
    )
    assert ans.status_code == 200, ans.text
    assert ans.json()["files"] == ["WELLS.INC"]
    inc = client.get(f"/cases/{case_id}/artifacts/schedule_source")
    assert inc.status_code == 200
    assert inc.content.startswith(b"WELSPECS")
    xlsx = client.get(f"/cases/{case_id}/artifacts/excel")
    assert xlsx.status_code == 200
    assert xlsx.content.startswith(b"PK")
    feed = client.get(f"/cases/{case_id}")
    assert "WELLS.INC" in feed.json()["attached_files"]
    kinds = [e["kind"] for e in client.get(f"/cases/{case_id}/events").json()["events"]]
    assert "hitl.answered" in kinds


def test_event_lane_handoff_directions() -> None:
    from app.cases_api import event_lane, event_to_turn

    created = event_lane({"kind": "case.created", "actor": "user"})
    assert created == ("user", "orchestrator", "out")

    handoff = event_lane({"kind": "agent.handoff", "actor": "orchestrator", "agent_id": "schedule_builder"})
    assert handoff == ("orchestrator", "schedule_builder", "out")

    result = event_lane({"kind": "agent.result", "actor": "schedule_builder", "agent_id": "schedule_builder"})
    assert result == ("orchestrator", "schedule_builder", "in")

    status = event_lane({"kind": "orchestrator.status", "actor": "orchestrator"})
    assert status[0] == "orchestrator" and status[2] == "none"

    progress = event_lane({"kind": "agent.progress", "actor": "excel_extractor", "agent_id": "excel_extractor"})
    assert progress == ("excel_extractor", None, "none")

    accepted = event_to_turn({"kind": "agent.accepted", "actor": "excel_extractor", "agent_id": "excel_extractor", "status_message": "Разбираю Excel"})
    assert accepted["lane_dir"] == "none"
    assert accepted["from_role"] == "excel_extractor"
    assert accepted["to_role"] == "excel_extractor"
    assert accepted["kind"] == "event"

    turn = event_to_turn({"kind": "agent.handoff", "actor": "orchestrator", "agent_id": "schedule_builder", "status_message": "Пишу INC"})
    assert turn["lane_dir"] == "out"
    assert turn["from_role"] == "orchestrator"
    assert turn["to_role"] == "schedule_builder"
    assert turn["kind"] == "handoff"


def test_schedule_artifact_download_from_state(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings
    from app import control_plane

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post("/cases", data={"task_description": "INC out", "requested_by": "tester"})
    assert res.status_code == 200, res.text
    case_id = res.json()["case_id"]
    row = control_plane.get_case(case_id)
    state = dict(row["state"])
    state["artifacts"] = {
        **(state.get("artifacts") or {}),
        "schedule_source": {"filename": "FORECAST.INC", "artifact_id": "schedule_source"},
        "schedule_out": "DATES\n  1 JAN 2020 /\n/\nWELSPECS\n/",
    }
    control_plane.update_case(case_id, state=state, status="done")
    feed = client.get(f"/cases/{case_id}")
    assert feed.status_code == 200
    art = feed.json()["schedule_artifact"]
    assert art["available"] is True
    assert art["filename"] == "FORECAST_result.INC"
    assert art["download_path"] == f"/cases/{case_id}/schedule"
    assert art["label"] == "Скачать результат"
    dl = client.get(f"/cases/{case_id}/schedule")
    assert dl.status_code == 200
    assert b"DATES" in dl.content
    assert "FORECAST_result.INC" in dl.headers.get("content-disposition", "")


def test_result_filename_does_not_reuse_baseline_name() -> None:
    from app.cases_api import _result_filename

    assert _result_filename({"schedule_source": {"filename": "baseline.inc"}}) == "baseline_result.inc"
    assert _result_filename({"schedule_source": {"filename": "FORECAST.INC"}}) == "FORECAST_result.INC"
    assert _result_filename({"schedule_out": {"filename": "custom_out.inc"}}) == "custom_out.inc"


def test_collapse_duplicate_status_and_agent_result() -> None:
    from app.cases_api import collapse_duplicate_events

    msg = "schedule_out уже есть — завершаю."
    result = {
        "kind": "agent.result",
        "actor": "schedule_builder",
        "agent_id": "schedule_builder",
        "task_id": "TASK-2",
        "status_message": "Перепривязал 2 скважин в DKS",
        "event_id": 1,
    }
    rows = collapse_duplicate_events(
        [
            result,
            {**result, "event_id": 2, "task_id": "", "payload": {"data_keys": ["edits"]}},
            {"kind": "orchestrator.status", "actor": "orchestrator", "status_message": msg, "event_id": 3},
            {"kind": "orchestrator.decision", "actor": "orchestrator", "status_message": msg, "event_id": 4},
            {"kind": "case.finished", "actor": "orchestrator", "status_message": msg, "event_id": 5},
        ]
    )
    kinds = [row["kind"] for row in rows]
    assert kinds == ["agent.result", "case.finished"]
    assert rows[0]["event_id"] == 1
    assert rows[1]["event_id"] == 5


def test_agent_result_post_is_idempotent(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings
    from app import control_plane

    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_create", lambda case_id: None)
    res = client.post("/cases", data={"task_description": "idem", "requested_by": "tester"})
    case_id = res.json()["case_id"]
    body = {
        "kind": "agent.result",
        "actor": "schedule_builder",
        "agent_id": "schedule_builder",
        "task_id": "TASK-2",
        "status": "completed",
        "status_message": "Перепривязал 2 скважин в DKS",
    }
    first = client.post(f"/cases/{case_id}/events", json=body)
    second = client.post(f"/cases/{case_id}/events", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    assert first.json()["event"]["event_id"] == second.json()["event"]["event_id"]
    raw = control_plane.list_events(case_id)
    results = [row for row in raw if row["kind"] == "agent.result"]
    assert len(results) == 1
    control_plane.append_event(
        case_id,
        kind="orchestrator.status",
        actor="orchestrator",
        status_message="schedule_out уже есть — завершаю.",
    )
    control_plane.append_event(
        case_id,
        kind="orchestrator.decision",
        actor="orchestrator",
        status_message="schedule_out уже есть — завершаю.",
    )
    control_plane.append_event(
        case_id,
        kind="case.finished",
        actor="orchestrator",
        status="done",
        status_message="schedule_out уже есть — завершаю.",
    )
    feed_kinds = [row["kind"] for row in client.get(f"/cases/{case_id}").json()["events"]]
    assert feed_kinds[-2:] == ["agent.result", "case.finished"]
    assert feed_kinds.count("orchestrator.status") == 0
    assert feed_kinds.count("orchestrator.decision") == 0


def test_post_run_restarts_failed_and_done_cases(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://127.0.0.1:9/webhook/mas-orchestrator-step")
    from app.settings import Settings
    from app import control_plane
    from app.contracts import MAX_STEPS

    invoked: list[str] = []
    monkeypatch.setattr("app.cases_api.get_settings", lambda: Settings())
    monkeypatch.setattr("app.cases_api._invoke_step", lambda case_id: invoked.append(case_id))
    res = client.post("/cases", data={"task_description": "keep goal", "requested_by": "tester"})
    assert res.status_code == 200, res.text
    case_id = res.json()["case_id"]
    invoked.clear()
    state = dict(control_plane.get_case(case_id)["state"])
    state["data"] = {"facts": [1]}
    state["step_count"] = MAX_STEPS
    state["last_error"] = {"message": "boom"}
    control_plane.update_case(case_id, state=state, status="failed")

    feed = client.get(f"/cases/{case_id}").json()
    assert feed["restartable"] is True
    assert feed["status"] == "failed"
    listed = client.get("/cases").json()["cases"]
    row = next(item for item in listed if item["case_id"] == case_id)
    assert row["restartable"] is True

    skipped = client.post(f"/cases/{case_id}/run")
    assert skipped.status_code == 200
    assert skipped.json()["skipped"] is True
    assert skipped.json()["accepted"] is False
    assert invoked == []
    assert control_plane.get_case(case_id)["status"] == "failed"

    restarted = client.post(f"/cases/{case_id}/run", json={"action": "retry"})
    assert restarted.status_code == 200, restarted.text
    body = restarted.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["skipped"] is False
    assert body["status"] == "running"
    assert body["restartable"] is False
    assert invoked == [case_id]
    after = control_plane.get_case(case_id)
    assert after["status"] == "running"
    assert after["state"]["goal"] == "keep goal"
    assert after["state"]["data"] == {"facts": [1]}
    assert after["state"].get("last_error") is None
    assert int(after["state"].get("step_count") or 0) == 0
    kinds = [event["kind"] for event in control_plane.list_events(case_id)]
    assert "orchestrator.status" in kinds
    assert client.get(f"/cases/{case_id}").json()["restartable"] is False

    invoked.clear()
    control_plane.update_case(case_id, status="done")
    assert client.get(f"/cases/{case_id}").json()["restartable"] is True
    again = client.post(f"/cases/{case_id}/run", json={"action": "restart"})
    assert again.status_code == 200
    assert again.json()["accepted"] is True
    assert invoked == [case_id]
    assert control_plane.get_case(case_id)["status"] == "running"
