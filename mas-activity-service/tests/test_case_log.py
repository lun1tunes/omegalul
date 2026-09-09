"""Developer log («Лог» tab): ``GET /cases/{id}/log`` — levels, sources, steps, n8n links, error merge."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import case_log, control_plane
from app.cases_api import collapse_duplicate_events
from app.main import app, reset_store


@pytest.fixture(autouse=True)
def _isolate(monkeypatch) -> None:
    for key in ("CONTROL_PLANE_PROXY_URL", "ORCHESTRATOR_WEBHOOK_URL", "N8N_BASE_URL", "ACTIVITY_STATE_PATH", "ACTIVITY_BINARIES_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONTROL_PLANE_REQUIRED", "false")
    reset_store()


client = TestClient(app)
EXEC = {"execution_id": "4711", "workflow_id": "wf-orch", "workflow_name": "MAS — Orchestrator"}


def _seed(case_id: str = "CASE-log-1") -> str:
    control_plane.create_case(case_id, "Сдвинуть даты ввода", initial_event={"kind": "case.created", "actor": "user", "status": "new", "status_message": "Задача создана"})
    ev = lambda **kw: control_plane.append_event(case_id, **kw)  # noqa: E731
    ev(kind="orchestrator.decision", actor="orchestrator", status_message="Передаю задачу агенту Excel.",
       payload={"action_type": "call_agent", "agent_id": "excel_extractor", "step_count": 1, "decision": {"action": {"type": "call_agent"}}, **EXEC})
    ev(kind="agent.handoff", actor="orchestrator", agent_id="excel_extractor", task_id="TASK-1", status_message="Передаю задачу агенту Excel.",
       handoff_message="Извлеките даты ввода", payload={"task_id": "TASK-1", "agent_task": {"objective": "даты"}, **EXEC})
    ev(kind="agent.accepted", actor="excel_extractor", agent_id="excel_extractor", task_id="TASK-1", status="running", status_message="Принял задачу",
       payload={"source": "excel-extractor-agent-workflow", "execution_id": "4712", "workflow_id": "wf-excel", "workflow_name": "Agent — Excel Extractor"})
    ev(kind="trace.tool", actor="excel_extractor", agent_id="excel_extractor", task_id="TASK-1", status_message="extract_commissioning → ошибка column_not_found · 12 мс · вызов 1",
       payload={"tool": "extract_commissioning", "ok": False, "error": "column_not_found", "duration_ms": 12, "call": 1, "args": {"well_column": "Скв"}})
    ev(kind="trace.tool", actor="excel_extractor", agent_id="excel_extractor", task_id="TASK-1", status_message="extract_commissioning → ok · 340 мс · вызов 2",
       payload={"tool": "extract_commissioning", "ok": True, "duration_ms": 340, "call": 2, "args": {"well_column": "Скважина"}})
    ev(kind="agent.result", actor="excel_extractor", agent_id="excel_extractor", task_id="TASK-1", status="completed", status_message="Даты ввода: 14 скважин.",
       payload={"data_keys": ["facts"], **EXEC})
    ev(kind="orchestrator.decision", actor="orchestrator", status_message="Инженер принял результат — завершаю задачу.",
       payload={"action_type": "finish", "agent_id": None, "step_count": 2, "guard": "human_accepted", **EXEC})
    ev(kind="hitl.request", actor="orchestrator", status="waiting_user", status_message="Какие скважины оставить?", payload={"question_id": "Q-2", "question": "Какие скважины оставить?"})
    ev(kind="hitl.answered", actor="user", status="answered", status_message="Пользователь ответил: все", payload={"question_id": "Q-2", "answer": "все"})
    return case_id


def test_records_levels_sources_steps_and_links() -> None:
    case_id = _seed()
    log = case_log.build_case_log(case_id, n8n_base="https://n8n.corp")
    assert log is not None
    records = log["records"]
    by_kind = {}
    for record in records:
        by_kind.setdefault(record["kind"], []).append(record)

    assert by_kind["case.created"][0]["level"] == "info" and by_kind["case.created"][0]["source"] == "engineer" and by_kind["case.created"][0]["step"] == 0
    first, second = by_kind["orchestrator.decision"]
    assert first["title"] == "Решение: call_agent → excel_extractor" and first["level"] == "info" and first["step"] == 1
    assert first["execution_url"] == "https://n8n.corp/workflow/wf-orch/executions/4711" and first["detail"]["decision"] == {"action": {"type": "call_agent"}}
    assert second["title"] == "Решение: finish · guard human_accepted" and second["level"] == "warn" and second["step"] == 2

    handoff = by_kind["agent.handoff"][0]
    assert handoff["source"] == "orchestrator" and handoff["detail"]["handoff_message"] == "Извлеките даты ввода" and handoff["detail"]["agent_task"] == {"objective": "даты"}
    accepted = by_kind["agent.accepted"][0]
    assert accepted["source"] == "agent:excel_extractor" and accepted["execution_url"] == "https://n8n.corp/workflow/wf-excel/executions/4712"

    failed_call, ok_call = by_kind["trace.tool"]
    assert failed_call["level"] == "warn" and failed_call["title"] == "excel_extractor: extract_commissioning → ошибка column_not_found" and failed_call["duration_ms"] == 12
    assert ok_call["level"] == "debug" and ok_call["title"] == "excel_extractor: extract_commissioning → ok" and ok_call["step"] == 1

    assert by_kind["hitl.request"][0]["source"] == "orchestrator" and by_kind["hitl.request"][0]["title"] == "Вопрос инженеру Q-2"
    assert by_kind["hitl.answered"][0]["source"] == "engineer" and by_kind["hitl.answered"][0]["step"] == 2

    summary = log["summary"]
    assert summary["steps"] == 2 and summary["tool_calls"] == 2 and summary["hitl_rounds"] == 1 and summary["handoffs"] == 1
    assert summary["errors"] == 0 and summary["warnings"] == 2 and summary["agents"] == ["excel_extractor"]
    steps = {s["step"]: s for s in log["steps"]}
    assert steps[0]["title"] == "Старт" and steps[1]["agent_id"] == "excel_extractor" and steps[1]["tool_calls"] == 2 and steps[1]["level"] == "warn"
    assert steps[2]["action_type"] == "finish" and steps[2]["duration_ms"] is not None


def test_finish_decision_is_one_chat_line_but_its_own_log_step() -> None:
    """Parse decision now writes orchestrator.decision (action_type finish, plan counters) right before
    case.finished with the same status_message: the chat shows one final line, the developer log shows
    the last step with «Решение: finish» and the plan counters (O13 tail)."""
    summary = "Сдвинул даты 4 скважин; новый schedule приложен."
    decision = {"kind": "orchestrator.decision", "actor": "orchestrator", "status_message": summary, "event_id": 7,
                "payload": {"action_type": "finish", "agent_id": None, "step_count": 3, "plan": {"total": 2, "open": 0, "accepted": 0, "rejected": 0},
                            "verification": {"all_covered": True, "goal_parts": []}, **EXEC}}
    finished = {"kind": "case.finished", "actor": "orchestrator", "status": "done", "status_message": summary, "event_id": 8,
                "payload": {"action_type": "finish", "summary_for_human": summary, "plan": [{"id": "p1", "status": "done"}, {"id": "p2", "status": "done"}], **EXEC}}
    chat = collapse_duplicate_events([decision, finished])
    assert [e["kind"] for e in chat] == ["case.finished"]
    records = case_log.records_from_events([decision, finished], n8n_base="https://n8n.corp")
    assert [r["kind"] for r in records] == ["orchestrator.decision", "case.finished"] and {r["step"] for r in records} == {3}
    assert records[0]["title"] == "Решение: finish" and records[0]["level"] == "info" and records[0]["detail"]["plan"] == {"total": 2, "open": 0, "accepted": 0, "rejected": 0}
    (step,) = case_log.step_groups(records)
    assert step["step"] == 3 and step["action_type"] == "finish" and step["decision"] == "Решение: finish"


def test_error_traces_merge_into_node_error_records() -> None:
    case_id = _seed("CASE-log-err")
    trace = control_plane.append_error_trace(case_id=case_id, execution_id="4712", workflow_name="Agent — Excel Extractor", node_name="Excel Extractor AI Agent",
                                             error_message="Qwen returned 502", error_type="NodeApiError", stack="Error: 502\n  at …")
    control_plane.append_event(case_id, kind="system.node_error", actor="n8n", status="error", status_message="Упал узел Excel Extractor AI Agent",
                               payload={"error_id": trace["error_id"], "execution_id": "4712", "execution_url": "https://n8n.corp/workflow/wf-excel/executions/4712", "node_name": "Excel Extractor AI Agent"})
    # An error row without an event (older error workflow) still shows up, attributed to the running step.
    control_plane.append_error_trace(case_id=case_id, execution_id="4799", workflow_name="MAS — Orchestrator", node_name="Call agent (n8n)",
                                     error_message="sub-workflow failed", error_type="WorkflowOperationError", stack="")

    log = case_log.build_case_log(case_id, n8n_base="https://n8n.corp")
    assert log is not None
    errors = [r for r in log["records"] if r["kind"] == "system.node_error"]
    assert len(errors) == 2 and all(r["level"] == "error" and r["source"] == "n8n" for r in errors)
    merged = next(r for r in errors if r["detail"].get("error_id") == trace["error_id"])
    assert merged["detail"]["stack"] == "Error: 502\n  at …" and merged["detail"]["error_message"] == "Qwen returned 502"
    assert merged["title"] == "n8n: упал узел Excel Extractor AI Agent (Agent — Excel Extractor)"
    assert merged["execution_url"] == "https://n8n.corp/workflow/wf-excel/executions/4712"
    orphan = next(r for r in errors if r["detail"].get("node_name") == "Call agent (n8n)")
    assert str(orphan["seq"]).startswith("error-") and orphan["step"] == 2
    assert log["summary"]["errors"] == 2


def test_log_endpoint_json_and_ndjson_and_chat_hides_trace() -> None:
    case_id = _seed("CASE-log-api")
    res = client.get(f"/cases/{case_id}/log")
    assert res.status_code == 200
    body = res.json()
    assert body["case_id"] == case_id and body["summary"]["tool_calls"] == 2 and len(body["records"]) == 10
    # No n8n configured → no links, but the log still renders.
    assert all(r["execution_url"] is None for r in body["records"])

    nd = client.get(f"/cases/{case_id}/log", params={"format": "ndjson"})
    assert nd.status_code == 200 and nd.headers["content-type"].startswith("application/x-ndjson")
    assert nd.headers["content-disposition"] == f'attachment; filename="{case_id}.log.ndjson"'
    lines = [json.loads(line) for line in nd.text.strip().splitlines()]
    assert lines[0]["type"] == "case" and lines[0]["summary"]["steps"] == 2
    assert [line["type"] for line in lines[1:]] == ["record"] * 10 and lines[5]["kind"] == "trace.tool"

    # The engineer's chat never shows trace rows; the developer log keeps them.
    feed = client.get(f"/cases/{case_id}").json()
    assert not any(t["event_type"].startswith("trace.") for t in feed["activity"])
    assert not any(e["kind"].startswith("trace.") for e in feed["events"])
    events = client.get(f"/cases/{case_id}/events").json()["events"]
    assert not any(e["kind"].startswith("trace.") for e in events)
    assert collapse_duplicate_events([{"kind": "trace.note", "actor": "x"}]) == []

    assert client.get("/cases/CASE-nope/log").status_code == 404


def test_case_cancelled_is_info_from_engineer() -> None:
    case_id = _seed("CASE-log-cancel")
    control_plane.append_event(
        case_id,
        kind="case.cancelled",
        actor="user",
        status="cancelled",
        status_message="Задача закрыта инженером",
    )
    log = case_log.build_case_log(case_id, n8n_base="https://n8n.corp")
    cancelled = [r for r in log["records"] if r["kind"] == "case.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["level"] == "info"
    assert cancelled[0]["source"] == "engineer"
    assert cancelled[0]["title"] == "Задача закрыта"



def test_post_event_with_execution_id_maps_execution_to_case_and_accepts_trace_kinds() -> None:
    case_id = _seed("CASE-log-exec")
    res = client.post(f"/cases/{case_id}/events", json={
        "kind": "agent.accepted", "actor": "demo_agent", "agent_id": "demo_agent", "task_id": "TASK-9", "status": "running",
        "status_message": "Принял задачу", "payload": {"execution_id": "9001", "workflow_id": "wf-demo", "workflow_name": "Agent — Demo"},
    })
    assert res.status_code == 200
    # The Error Trigger of the agent workflow looks the case up by its own execution id.
    assert control_plane.case_id_for_execution("9001") == case_id

    note = client.post(f"/cases/{case_id}/events", json={
        "kind": "trace.note", "actor": "demo_agent", "agent_id": "demo_agent", "task_id": "TASK-9",
        "status_message": "фон: 3 файла", "payload": {"title": "фон: 3 файла", "level": "info", "files": 3},
    })
    assert note.status_code == 200
    record = next(r for r in client.get(f"/cases/{case_id}/log").json()["records"] if r["kind"] == "trace.note")
    assert record["level"] == "info" and record["title"] == "demo_agent: фон: 3 файла" and record["detail"]["files"] == 3
    assert client.post(f"/cases/{case_id}/events", json={"kind": "trace.bogus", "actor": "x"}).status_code == 422
