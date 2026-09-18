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
    assert accepted["actor"] == "excel_extractor" and accepted["status"] == "running"

    failed_call, ok_call = by_kind["trace.tool"]
    assert failed_call["level"] == "warn" and failed_call["title"] == "excel_extractor: extract_commissioning → ошибка column_not_found" and failed_call["duration_ms"] == 12
    assert ok_call["level"] == "debug" and ok_call["title"] == "excel_extractor: extract_commissioning → ok" and ok_call["step"] == 1

    assert by_kind["hitl.request"][0]["source"] == "orchestrator" and by_kind["hitl.request"][0]["title"] == "Вопрос инженеру Q-2"
    assert by_kind["hitl.answered"][0]["source"] == "engineer" and by_kind["hitl.answered"][0]["step"] == 2

    summary = log["summary"]
    assert summary["steps"] == 2 and summary["tool_calls"] == 2 and summary["hitl_rounds"] == 1 and summary["handoffs"] == 1
    assert summary["errors"] == 0 and summary["warnings"] == 2 and summary["agents"] == ["excel_extractor"]
    assert summary["tool_calls_by_agent"] == {"excel_extractor": 2}
    assert summary["llm_truncated"] == 0 and summary["rag_empty"] == 0 and summary["kb_calls"] == 0
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


def test_threshold_violations_name_the_log_record() -> None:
    """α2: a live fail must point at seq/title, not only a counter (no n8n)."""
    records = case_log.build_case_log(_seed("CASE-log-thresh"))["records"]
    clean = case_log.threshold_violations(records, max_warnings=2, max_steps=6, max_tool_calls_per_agent=4)
    assert clean == []
    warn = case_log.threshold_violations(records, max_warnings=0, max_steps=6, max_tool_calls_per_agent=4)
    assert any("warnings=2 > 0" in p and "extract_commissioning" in p and "seq=" in p for p in warn)
    steps = case_log.threshold_violations(records, max_warnings=10, max_steps=1, max_tool_calls_per_agent=1)
    assert any("steps=2 > 1" in p and "seq=" in p for p in steps)
    tools = case_log.threshold_violations(records, max_warnings=10, max_steps=6, max_tool_calls_per_agent=1)
    assert any("tool_calls[excel_extractor]=2 > 1" in p and "seq=" in p for p in tools)
    skipped = case_log.threshold_violations(
        records, max_warnings=None, max_steps=None, max_tool_calls_per_agent=None, max_llm_truncated=None
    )
    assert skipped == []


def test_unparsed_and_timeout_are_named_in_the_log() -> None:
    """CASE-6aa50b14: parser/timeout must show the real reason, not a generic URL hint."""
    case_id = "CASE-log-parse"
    control_plane.create_case(case_id, "тест", initial_event={"kind": "case.created", "actor": "user", "status": "new"})
    control_plane.append_event(
        case_id,
        kind="orchestrator.decision",
        actor="orchestrator",
        status_message="Не удалось разобрать следующий шаг. Формулирую решение заново.",
        payload={
            "action_type": "continue",
            "guard": "decision_unparsed",
            "llm_parse": {
                "error": "llm_truncated_empty",
                "finish_reason": "length",
                "completion_tokens": 2048,
                "reasoning_tokens": 2048,
                "attempt": 1,
            },
        },
    )
    control_plane.append_event(
        case_id,
        kind="orchestrator.status",
        actor="orchestrator",
        status="timeout",
        status_message="Оркестратор не подтвердил шаг за отведённое время. Задача может ещё выполняться — смотрите лог.",
        payload={"reason": "orchestrator_timeout", "elapsed_s": 181.2, "timeout_s": 180.0},
    )
    log = case_log.build_case_log(case_id)
    decision = next(r for r in log["records"] if r["kind"] == "orchestrator.decision")
    timeout = next(r for r in log["records"] if r["kind"] == "orchestrator.status")
    assert decision["level"] == "warn"
    assert "decision_unparsed" in decision["title"]
    assert "llm_truncated_empty" in decision["title"]
    assert "length" in decision["title"]
    assert "2048 tok" in decision["title"]
    assert "think 2048" in decision["title"]
    assert timeout["level"] == "warn"
    assert timeout["title"] == "Оркестратор: timeout 181.2s / 180.0s"
    assert log["summary"]["warnings"] == 1
    only_timeout = [r for r in log["records"] if r["kind"] == "orchestrator.status"]
    assert case_log.threshold_violations(only_timeout, max_warnings=0, max_steps=6, max_tool_calls_per_agent=4) == []
    assert case_log.summarize(only_timeout, status="running")["warnings"] == 0


def test_metrics_cases_since_excludes_older_and_omits_records() -> None:
    old_id = _seed("CASE-metrics-old")
    when = "2020-01-01T00:00:00+00:00"
    control_plane._CASES[old_id]["updated_at"] = when
    for event in control_plane._EVENTS.get(old_id, []):
        event["created_at"] = when
    new_id = _seed("CASE-metrics-new")
    bad = client.get("/metrics/cases", params={"since": "not-a-date"})
    assert bad.status_code == 400
    body = client.get("/metrics/cases", params={"since": "2024-01-01T00:00:00Z"}).json()
    ids = [c["case_id"] for c in body["cases"]]
    assert new_id in ids and old_id not in ids
    assert body["totals"]["count"] == 1
    row = next(c for c in body["cases"] if c["case_id"] == new_id)
    assert "records" not in row and "summary" in row
    assert row["summary"]["tool_calls_by_agent"] == {"excel_extractor": 2}
    assert "warnings" in row["summary"] and "steps" in row["summary"]
    assert "llm_truncated" in row["summary"] and "rag_empty" in row["summary"] and "kb_calls" in row["summary"]
    all_body = client.get("/metrics/cases").json()
    all_ids = {c["case_id"] for c in all_body["cases"]}
    assert old_id in all_ids and new_id in all_ids
    assert all_body["since"] is None


def test_trace_rag_llm_resume_titles_gaps_and_chat_hide() -> None:
    """O25/O27/R9, ревизия 43, executions #228559: tokens on success; resume in log not chat."""
    import sys
    from pathlib import Path

    kit_root = Path(__file__).resolve().parents[2] / "mas-agent-kit"
    sys.path.insert(0, str(kit_root))
    from mas_agent_kit.activity import EVENT_KINDS as KIT_KINDS
    from app.contracts import EVENT_KINDS as ACT_KINDS

    assert set(KIT_KINDS) <= set(ACT_KINDS)
    assert "orchestrator.resume" in ACT_KINDS and "orchestrator.resume" not in KIT_KINDS
    assert "trace.rag" in ACT_KINDS and "trace.llm" in ACT_KINDS

    case_id = "CASE-log-obs"
    control_plane.create_case(case_id, "Сдвинуть даты", initial_event={"kind": "case.created", "actor": "user", "status": "new", "status_message": "Задача создана"})
    control_plane.append_event(
        case_id,
        kind="orchestrator.decision",
        actor="orchestrator",
        status_message="Передаю задачу агенту Excel.",
        payload={
            "action_type": "call_agent",
            "agent_id": "excel_extractor",
            "step_count": 1,
            "llm": {"model": "qwen/qwen3.6-27b", "prompt_tokens": 1200, "completion_tokens": 80, "reasoning_tokens": 0, "finish_reason": "stop"},
            "rag": {"query": "даты ввода скважин", "status": "ready", "cards": [{"knowledge_id": "route-mas-thin-orchestrator", "rrf_score": 0.016, "branches": ["semantic"]}]},
            **EXEC,
        },
    )
    control_plane.append_event(
        case_id,
        kind="trace.rag",
        actor="orchestrator",
        status_message="База знаний: ready · 1 карточек",
        payload={"caller": "orchestrator", "query": "даты ввода скважин", "status": "ready", "findings": [], "cards": [{"knowledge_id": "route-mas-thin-orchestrator", "rrf_score": 0.016, "branches": ["semantic"]}], **EXEC},
    )
    control_plane.append_event(
        case_id,
        kind="trace.llm",
        actor="orchestrator",
        status_message="decision: stop · 1280 tok",
        payload={"role": "decision", "prompt_tokens": 1200, "completion_tokens": 80, "finish_reason": "stop", **EXEC},
    )
    control_plane.append_event(
        case_id,
        kind="trace.rag",
        actor="schedule_builder",
        agent_id="schedule_builder",
        status_message="База знаний: unavailable · 0 карточек",
        payload={"caller": "schedule_builder", "query": "сдвинуть даты", "status": "unavailable", "findings": [{"code": "SCHEMA_KEYWORD_SCOPE_REQUIRED"}], "cards": []},
    )
    control_plane.append_event(
        case_id,
        kind="orchestrator.resume",
        actor="agent",
        agent_id="demo_agent",
        task_id="TASK-long",
        status="running",
        status_message="Продолжение по событию агента или системы",
        payload={"source": "agent", "task_id": "TASK-long", **EXEC},
    )
    assert client.post(f"/cases/{case_id}/events", json={
        "kind": "trace.rag", "actor": "excel_extractor", "agent_id": "excel_extractor",
        "status_message": "База знаний: ready · 1 карточек",
        "payload": {"caller": "excel_extractor", "query": "даты", "status": "ready", "cards": [{"knowledge_id": "excel-agent-trust-boundary"}]},
    }).status_code == 200
    assert client.post(f"/cases/{case_id}/events", json={
        "kind": "trace.llm", "actor": "orchestrator",
        "status_message": "decision: stop · 10 tok",
        "payload": {"role": "decision", "prompt_tokens": 8, "completion_tokens": 2, "finish_reason": "stop"},
    }).status_code == 200
    assert client.post(f"/cases/{case_id}/events", json={"kind": "trace.bogus", "actor": "x"}).status_code == 422

    log = case_log.build_case_log(case_id, n8n_base="https://n8n.corp")
    by_kind: dict[str, list] = {}
    for record in log["records"]:
        by_kind.setdefault(record["kind"], []).append(record)

    decision = by_kind["orchestrator.decision"][0]
    assert "1280 tok" in decision["title"] and "stop" in decision["title"]
    assert decision["level"] == "info"
    rag_ok = next(r for r in by_kind["trace.rag"] if r["detail"].get("status") == "ready" and r["source"] == "orchestrator")
    assert rag_ok["level"] == "debug"
    assert rag_ok["title"].startswith("База знаний: ready · 1 карточек")
    assert rag_ok["detail"]["cards"][0]["knowledge_id"] == "route-mas-thin-orchestrator"
    rag_empty = next(r for r in by_kind["trace.rag"] if r["detail"].get("status") == "unavailable")
    assert rag_empty["level"] == "warn"
    assert "SCHEMA_KEYWORD_SCOPE_REQUIRED" in rag_empty["title"]
    llm = by_kind["trace.llm"][0]
    assert llm["level"] == "debug" and llm["title"].startswith("decision: stop · 1280 tok")
    resume = by_kind["orchestrator.resume"][0]
    assert resume["title"].startswith("Возобновление: источник agent")
    assert resume["execution_url"] == "https://n8n.corp/workflow/wf-orch/executions/4711"
    assert log["records"][0].get("gap_ms") is None

    feed = client.get(f"/cases/{case_id}").json()
    assert not any(str(t.get("event_type") or "").startswith("trace.") for t in feed["activity"])
    assert not any(e.get("kind") == "orchestrator.resume" for e in feed["events"])
    assert not any(str(e.get("kind") or "").startswith("trace.") for e in feed["events"])
    events = client.get(f"/cases/{case_id}/events").json()["events"]
    assert not any(e.get("kind") == "orchestrator.resume" for e in events)
    assert collapse_duplicate_events([{"kind": "orchestrator.resume", "status_message": "Продолжение по событию агента или системы"}]) == []

    empty_only = [r for r in log["records"] if r["kind"] == "trace.rag" and r["detail"].get("status") == "unavailable"]
    empty_sum = case_log.summarize(empty_only, status="running")
    assert empty_sum["warnings"] == 0
    assert empty_sum["rag_empty"] == 1
    assert empty_sum["kb_calls"] == 0
    assert empty_sum["kb_calls_by_agent"] == {}
    assert case_log.threshold_violations(empty_only, max_warnings=0, max_steps=6, max_tool_calls_per_agent=4) == []
    assert any(
        "rag_empty=1 > 0" in p
        for p in case_log.threshold_violations(
            empty_only, max_warnings=0, max_steps=6, max_tool_calls_per_agent=4, max_rag_empty=0
        )
    )
    failed_rag = case_log.log_record({"kind": "trace.rag", "payload": {"status": "failed", "cards": []}})
    assert failed_rag["level"] == "warn"
    assert case_log.summarize([failed_rag], status="running")["warnings"] == 1
    abstain_rag = case_log.log_record({
        "kind": "trace.rag", "agent_id": "schedule_builder",
        "payload": {"status": "abstain", "phase": "initial", "findings": [{"code": "SCHEMA_KEYWORD_SCOPE_REQUIRED"}], "cards": []},
    })
    assert abstain_rag["level"] == "warn"
    abstain_sum = case_log.summarize([abstain_rag], status="running")
    assert abstain_sum["warnings"] == 0 and abstain_sum["rag_empty"] == 1 and abstain_sum["kb_calls"] == 0
    truncated = case_log.log_record({"kind": "trace.llm", "payload": {"role": "decision", "finish_reason": "length", "prompt_tokens": 1, "completion_tokens": 0}})
    assert truncated["level"] == "warn"
    trunc_sum = case_log.summarize([truncated], status="running")
    assert trunc_sum["warnings"] == 0 and trunc_sum["llm_truncated"] == 1
    trunc_fail = case_log.threshold_violations([truncated], max_warnings=0, max_steps=6, max_tool_calls_per_agent=4)
    assert any("llm_truncated=1 > 0" in p for p in trunc_fail)

    stamped = case_log.records_from_events([
        {"kind": "case.created", "created_at": "2026-09-15T10:00:00+00:00", "payload": {}},
        {"kind": "trace.llm", "created_at": "2026-09-15T10:00:02+00:00", "payload": {"role": "decision", "finish_reason": "stop", "prompt_tokens": 1, "completion_tokens": 1}},
    ])
    assert stamped[0]["gap_ms"] is None and stamped[1]["gap_ms"] == 2000


def test_knowledge_counts_and_kb_calls_floor() -> None:
    """8.4: kb_calls counts on-demand retrieve only; Attach (phase=initial) does not green the floor."""
    orch = case_log.log_record({
        "kind": "trace.rag", "actor": "orchestrator",
        "payload": {"caller": "orchestrator", "status": "ready", "phase": "initial", "cards": [{"knowledge_id": "route-mas-thin-orchestrator"}]},
    })
    excel = case_log.log_record({
        "kind": "trace.rag", "actor": "excel_extractor", "agent_id": "excel_extractor",
        "payload": {"caller": "excel_extractor", "status": "ready", "phase": "initial", "cards": [{"knowledge_id": "excel-agent-trust-boundary"}]},
    })
    builder = case_log.log_record({
        "kind": "trace.rag", "actor": "schedule_builder", "agent_id": "schedule_builder",
        "payload": {"caller": "schedule_builder", "status": "abstain", "phase": "initial", "findings": [{"code": "SCHEMA_KEYWORD_SCOPE_REQUIRED"}], "cards": []},
    })
    demand = case_log.log_record({
        "kind": "trace.rag", "actor": "schedule_builder", "agent_id": "schedule_builder",
        "payload": {"caller": "schedule_builder", "status": "ready", "phase": "on_demand", "cards": [{"knowledge_id": "wefac-v1"}]},
    })
    decision = case_log.log_record({
        "kind": "orchestrator.decision", "actor": "orchestrator",
        "payload": {"action_type": "continue", "llm_parse": {"error": "llm_truncated_empty", "finish_reason": "length"}},
    })
    summary = case_log.summarize([orch, excel, builder, demand, decision], status="running")
    assert summary["kb_calls"] == 1
    assert summary["kb_calls_by_agent"] == {"schedule_builder": 1}
    assert summary["rag_empty"] == 1
    assert summary["rag_empty_by_agent"] == {"schedule_builder": 1}
    assert summary["llm_truncated"] == 1
    default = case_log.threshold_violations([orch, excel, builder], max_warnings=0, max_steps=6, max_tool_calls_per_agent=4)
    assert default == []
    floor = case_log.threshold_violations(
        [orch, excel, builder], max_warnings=0, max_steps=6, max_tool_calls_per_agent=4, min_kb_calls=1
    )
    assert any("kb_calls=0 < 1" in p for p in floor)


def test_records_from_events_traces_before_decision_share_step() -> None:
    events = [
        {"kind": "case.created", "created_at": "2026-09-16T00:00:00+00:00"},
        {"kind": "trace.rag", "actor": "orchestrator", "payload": {"caller": "orchestrator", "status": "ready", "phase": "initial", "step_count": 1, "cards": []}},
        {"kind": "trace.llm", "actor": "orchestrator", "payload": {"role": "decision", "step_count": 1, "finish_reason": "stop", "prompt_preview": {"messages": [{"role": "system", "content": "x"}]}}},
        {"kind": "orchestrator.decision", "payload": {"action_type": "finish", "step_count": 1}},
        {"kind": "case.finished", "created_at": "2026-09-16T00:01:00+00:00"},
    ]
    records = case_log.records_from_events(events)
    assert [r["kind"] for r in records] == ["case.created", "trace.rag", "trace.llm", "orchestrator.decision", "case.finished"]
    assert records[0]["step"] == 0
    assert [r["step"] for r in records[1:]] == [1, 1, 1, 1]
    groups = case_log.step_groups(records)
    done = next(g for g in groups if g["step"] == 1)
    assert done["ended_at"] == "2026-09-16T00:01:00+00:00"
