from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mas_agent_kit import (
    ActivityClient,
    AgentService,
    CasePacket,
    SessionStore,
    ToolError,
    ToolRegistry,
    create_agent_app,
    flatten_artifacts,
    upstream_agent_data,
)

ROOT = Path(__file__).resolve().parents[2]

NESTED = {
    "excel": {"artifact_id": "excel", "filename": "dates.xlsx", "bytes": 10},
    "schedule": {
        "source": {"artifact_id": "schedule_source", "filename": "SCHEDULE.INC"},
        "includes": [{"artifact_id": "schedule_source_1", "filename": "WELLS.INC"}],
        "grdecl": [],
        "out": "DATES\n 1 JAN 2026 /\n/\n",
    },
    "trajectories": [{"artifact_id": "trajectory_1", "filename": "w1.dev"}],
    "attachments": [{"artifact_id": "excel_1", "filename": "params.xlsx", "role": "excel"}],
    "notes": {"artifact_id": "notes", "filename": "notes.txt"},
}


def test_flatten_matches_activity_state_shape() -> None:
    spec = importlib.util.spec_from_file_location("state_shape", ROOT / "mas-activity-service" / "app" / "state_shape.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ours = flatten_artifacts(NESTED)
    theirs = module.flatten_artifacts(NESTED)
    assert ours == theirs
    assert set(ours) == {"excel", "schedule_source", "schedule_source_1", "schedule_out", "trajectory_1", "excel_1", "notes"}
    assert ours["schedule_source_1"]["role"] == "schedule_include"
    assert ours["schedule_out"]["text"].startswith("DATES")
    flat = flatten_artifacts({"excel": {"filename": "a.xlsx"}, "schedule_source": "b.inc"})
    assert flat["schedule_source"] == {"artifact_id": "schedule_source", "filename": "b.inc", "role": "schedule_source"}


def test_upstream_data_merges_completed_agents_in_step_order() -> None:
    state = {
        "data": {"excel": {"facts": [{"well": "old"}], "omitted_keys": ["x"]}},
        "agents": {
            "b": {"status": "completed", "step": 3, "data": {"facts": [{"well": "new"}], "omitted_keys": ["y"]}},
            "a": {"status": "completed", "step": 1, "data": {"new_wells": [{"well": "N1"}]}},
            "c": {"status": "failed", "step": 4, "data": {"facts": []}},
        },
    }
    merged = upstream_agent_data(state)
    assert merged == {"facts": [{"well": "new"}], "new_wells": [{"well": "N1"}]}


class _FakeActivity(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []

    def log_message(self, *_: Any) -> None:  # quiet
        return

    def _send(self, code: int, payload: dict[str, Any] | bytes, headers: dict[str, str] | None = None) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        for key, value in (headers or {"Content-Type": "application/json"}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/state"):
            self._send(200, {"state": {"artifacts": NESTED, "agents": {"x": {"status": "completed", "step": 1, "data": {"facts": [1]}}}}})
        elif "/artifacts/" in self.path:
            self._send(200, b"WELSPECS\n/\n", {"Content-Type": "text/plain", "Content-Disposition": "attachment; filename=\"SCHEDULE.INC\""})
        else:
            self._send(404, {"detail": "no"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or ""
        entry: dict[str, Any] = {"path": self.path, "content_type": ctype}
        if ctype.startswith("application/json"):
            entry["json"] = json.loads(raw.decode("utf-8"))
        else:
            entry["raw"] = raw
        _FakeActivity.calls.append(entry)
        if self.path.endswith("/artifacts"):
            self._send(200, {"ok": True, "artifact": {"artifact_id": "report", "filename": "report.xlsx", "bytes": 5, "role": "attachment", "kind": "deliverable", "producer": "demo"}})
        else:
            self._send(200, {"ok": True})


@pytest.fixture
def activity_server():
    _FakeActivity.calls = []
    server = HTTPServer(("127.0.0.1", 0), _FakeActivity)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def test_activity_client_and_case_packet(activity_server: str) -> None:
    task = {"case_id": "CASE-1", "task_id": "T-1", "objective": "Сдвинь даты", "inputs": {"activity_base_url": activity_server, "rework_reason": "нет 1602"}, "context": {"hitl": {"answers": {"Q": {"choice": "keep"}}}}}
    activity = ActivityClient.from_task(task, agent_id="demo")
    assert activity.configured
    packet = CasePacket.load(task, activity)
    assert set(packet.artifacts) >= {"excel", "schedule_source", "excel_1"}
    assert [c["artifact_id"] for c in packet.cards(role="excel")] == ["excel", "excel_1"]
    assert packet.upstream_data() == {"facts": [1]}
    assert packet.engineer_answers() == [{"question_id": "Q", "choice": "keep"}]
    assert packet.rework_reason == "нет 1602"
    data, name = packet.download("schedule_source")
    assert data.startswith(b"WELSPECS") and name == "SCHEDULE.INC"

    assert activity.accepted("Принял задачу") is True
    assert activity.progress("Читаю", status="running", payload={"n": 1}) is True
    card = activity.upload("report", "report.xlsx", b"hello", "application/vnd.ms-excel", summary="Отчёт")
    assert card["artifact_id"] == "report"
    activity.finish_task({"task_id": "T-1", "status": "completed", "message": "Готово."})
    kinds = [c["json"]["kind"] for c in _FakeActivity.calls if "json" in c and "kind" in c["json"]]
    assert kinds == ["agent.accepted", "agent.progress"]
    assert _FakeActivity.calls[1]["json"]["payload"] == {"n": 1} and _FakeActivity.calls[1]["json"]["agent_id"] == "demo"
    upload = _FakeActivity.calls[2]
    assert upload["content_type"].startswith("multipart/form-data") and b'name="artifact_id"\r\n\r\nreport' in upload["raw"] and b"hello" in upload["raw"]
    resume = _FakeActivity.calls[3]["json"]
    assert resume == {"action": "resume", "source": "agent", "agent_id": "demo", "task_id": "T-1", "agent_result": {"task_id": "T-1", "status": "completed", "message": "Готово."}}

    silent = ActivityClient("http://127.0.0.1:9", agent_id="demo", case_id="CASE-1", timeout=0.2)
    assert silent.event("agent.progress", "x") is False and silent.case_state() == {}
    assert ActivityClient("", agent_id="demo").configured is False


class _DemoAgent(AgentService):
    agent_id = "demo"

    def __init__(self, root: Path):
        self.store = SessionStore(prefix="demo", root=root)
        self.tools = ToolRegistry(self.store)

        @self.tools.tool("echo", "Повторить", {"text": {"type": "string"}, "items": {"type": "array"}}, required=["text"])
        def echo(ctx, args):
            if not args.get("text"):
                raise ToolError("spec_incomplete", "Передай text.", missing=["text"])
            self.store_result(ctx.state, self.new_result(ctx.state, "completed", f"Повторил: {args['text']}", data={"items": args.get("items")}))
            return {"status": "completed"}

    def open_session(self, task):
        packet = self.packet(task)
        if not packet.objective:
            return self.not_opened(self.needs_input({"task_id": packet.task_id}, "Опишите, что нужно повторить."))
        state = self.store.create(self.base_state(packet))
        return self.opened(state, inspect={"words": len(packet.objective.split())})

    def result(self, state):
        return state.get("result") or self.needs_input(state, "Агент не получил текста для повтора.")

    def normalize_args(self, tool_name, args):
        if tool_name == "echo" and "message" in args and "text" not in args:
            args = {**args, "text": args["message"]}
        return args


def test_router_traces_every_tool_call_to_the_developer_log(tmp_path: Path, activity_server: str) -> None:
    """Each POST /agent-tools/{tool} → one trace.tool event: tool, ok/error, duration, bounded args (hidden from the chat).

    ``call`` counts LLM-driven calls only: internal registry runs (Excel inventory in ``open_session``) made the
    first live trace read ``call: 4`` for the single tool the model used (CASE-6a9fa129-5ae077).
    """
    agent = _DemoAgent(tmp_path)
    client = TestClient(create_agent_app(agent, title="demo"))
    task = {"case_id": "CASE-7", "task_id": "T-7", "objective": "Привет", "inputs": {"activity_base_url": activity_server}, "context": {}}
    sid = client.post("/agent-tools/open_session", json=task).json()["session_id"]
    with agent.store.lock(sid):  # service-internal run (inventory-style): in tool_history, not an LLM call
        agent.tools.run(agent.store.load(sid), "echo", {"text": "внутренний"})
    client.post("/agent-tools/echo", json={"session_id": sid})
    client.post("/agent-tools/echo", json={"session_id": sid, "text": "т" * 5000, "items": list(range(50))})
    traces = [c["json"] for c in _FakeActivity.calls if c["path"] == "/cases/CASE-7/events" and c["json"]["kind"] == "trace.tool"]
    assert len(traces) == 2
    bad, ok = traces
    assert bad["actor"] == "demo" and bad["task_id"] == "T-7" and bad["payload"]["tool"] == "echo" and bad["payload"]["ok"] is False
    assert bad["payload"]["error"] == "spec_incomplete" and bad["payload"]["call"] == 1 and bad["status_message"].startswith("echo → ошибка spec_incomplete")
    assert ok["payload"]["ok"] is True and ok["payload"]["call"] == 2 and ok["payload"]["duration_ms"] >= 0 and ok["payload"]["session_id"] == sid
    # Arguments are bounded, never dropped: the log must show what the LLM sent without growing the event table.
    assert ok["payload"]["args"]["text"].endswith("…") and len(ok["payload"]["args"]["text"]) < 600
    assert ok["payload"]["args"]["items"][-1] == "… ещё 30" and ok["payload"]["result"] == {"status": "completed"}
    # No Activity in the task → nothing is posted and nothing breaks.
    quiet = _DemoAgent(tmp_path / "quiet")
    qc = TestClient(create_agent_app(quiet, title="quiet"))
    qsid = qc.post("/agent-tools/open_session", json={"case_id": "CASE-8", "task_id": "T-8", "objective": "Привет", "inputs": {}, "context": {}}).json()["session_id"]
    before = len(_FakeActivity.calls)
    assert qc.post("/agent-tools/echo", json={"session_id": qsid, "text": "x"}).json()["ok"] is True
    assert len(_FakeActivity.calls) == before


def test_agent_app_routes(tmp_path: Path) -> None:
    agent = _DemoAgent(tmp_path)
    client = TestClient(create_agent_app(agent, title="demo"))
    assert client.get("/health").json() == {"ok": True, "agent_id": "demo", "tools": ["echo"]}
    task = {"case_id": "CASE-1", "task_id": "T-1", "objective": "Привет мир", "inputs": {}, "context": {"hitl": {"answers": {"Q": "да"}}}}
    opened = client.post("/agent-tools/open_session", json=task).json()
    assert opened["ok"] is True and opened["inspect"] == {"words": 2} and opened["engineer_answers"] == [{"question_id": "Q", "text": "да"}]
    sid = opened["session_id"]
    assert client.get(f"/sessions/{sid}/result").json()["status"] == "needs_input"
    bad = client.post("/agent-tools/echo", json={"session_id": sid}).json()
    assert bad["ok"] is False and bad["error"] == "spec_incomplete"
    ok = client.post("/agent-tools/echo", json={"session_id": sid, "message": "тест", "items": '["a"]'}).json()
    assert ok == {"ok": True, "status": "completed"}
    result = client.get(f"/sessions/{sid}/result").json()
    assert result["status"] == "completed" and result["message"] == "Повторил: тест" and result["data"] == {"items": ["a"]} and result["agent_id"] == "demo"
    assert client.post("/agent-tools/echo", json={"session_id": "demo_" + "0" * 32, "text": "x"}).status_code == 404
    assert client.post("/agent-tools/echo", json={"text": "x"}).status_code == 422
    assert client.post(f"/sessions/{sid}/close", json={}).json()["closed"] is True
    assert client.get(f"/sessions/{sid}/result").status_code == 404
    refused = client.post("/agent-tools/open_session", json={"case_id": "C", "task_id": "T-2", "objective": ""}).json()
    assert refused["ok"] is False and refused["status"] == "needs_input" and refused["result"]["requests"][0]["question"].startswith("Опишите")
