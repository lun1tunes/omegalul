"""Demo Agent — the contract every kit-based agent has to honour, exercised over HTTP like n8n does it.

The fake Activity records what the agent posts (events, the final ``resume source=agent``), so the
long-job path is proven without n8n: ``start_long_job`` → ``in_progress`` → progress lines → ``finish_task``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import DemoAgent, wells_in
from mas_agent_kit import create_agent_app, human_text_problems


class _FakeActivity(BaseHTTPRequestHandler):
    calls: list[dict[str, Any]] = []

    def log_message(self, *_: Any) -> None:
        return

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        # CASE-demo has one upstream agent that already found a well; any other case is empty.
        agents = {"excel_extractor": {"status": "completed", "step": 1, "data": {"facts": [{"well": "3001", "date": "2026-01-01"}]}}} if "/CASE-demo/" in self.path else {}
        self._send({"state": {"artifacts": {}, "agents": agents}})

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _FakeActivity.calls.append({"path": self.path, "json": json.loads(raw.decode("utf-8"))})
        self._send({"ok": True})


@pytest.fixture
def activity_url():
    _FakeActivity.calls = []
    server = HTTPServer(("127.0.0.1", 0), _FakeActivity)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


@pytest.fixture
def agent(tmp_path) -> DemoAgent:
    # Fast "long" job: 0.3 s total, progress every 0.1 s.
    return DemoAgent(root=str(tmp_path / "sessions"), job_seconds=0.3, progress_every_s=0.1)


@pytest.fixture
def client(agent: DemoAgent) -> TestClient:
    return TestClient(create_agent_app(agent, title="demo"))


def task(activity_url: str, objective: str, case_id: str = "CASE-demo") -> dict[str, Any]:
    return {"case_id": case_id, "task_id": "T-1", "agent_id": "demo_agent", "objective": objective, "inputs": {"activity_base_url": activity_url}, "context": {}}


def test_wells_in_is_deterministic() -> None:
    assert wells_in("скважины 1601, 1735 и 2012G; 1601 повторно; 12 — не скважина; 1.5 — тоже нет") == ["1601", "1735", "2012G"]


def test_health_lists_tools(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True and body["agent_id"] == "demo_agent"
    assert body["tools"] == ["count_wells", "start_long_job", "ask_engineer"]


def test_count_wells_completes_with_upstream_facts(client: TestClient, activity_url: str) -> None:
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Посчитай скважины 1601 и 1735")).json()
    assert opened["ok"] is True
    assert opened["inspect"] == {"wells_in_task": ["1601", "1735"], "upstream_fact_count": 1}
    sid = opened["session_id"]

    out = client.post("/agent-tools/count_wells", json={"session_id": sid}).json()
    assert out["status"] == "completed" and out["well_count"] == 3 and out["wells"] == ["1601", "1735", "3001"]

    result = client.get(f"/sessions/{sid}/result").json()
    assert result["status"] == "completed" and result["agent_id"] == "demo_agent" and result["task_id"] == "T-1"
    assert result["data"] == {"well_count": 3, "wells": ["1601", "1735", "3001"]}
    assert result["message"] == "Насчитал 3 скважины: 1601, 1735, 3001."
    assert human_text_problems(result["message"]) == []
    assert client.post(f"/sessions/{sid}/close", json={}).json()["closed"] is True


def test_no_wells_is_an_llm_error_not_an_engineer_question(client: TestClient, activity_url: str) -> None:
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Посчитай что-нибудь", case_id="CASE-empty")).json()
    out = client.post("/agent-tools/count_wells", json={"session_id": opened["session_id"]}).json()
    assert out["ok"] is False and out["error"] == "no_wells_found"
    assert "ask_engineer" in out["message"], "the message tells the LLM what to do next"
    # Nothing was stored: GET result falls back to the agent's own needs_input question.
    assert client.get(f"/sessions/{opened['session_id']}/result").json()["status"] == "needs_input"


def test_ask_engineer_stores_needs_input_in_russian(client: TestClient, activity_url: str) -> None:
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Сделай демонстрацию")).json()
    sid = opened["session_id"]
    bad = client.post("/agent-tools/ask_engineer", json={"session_id": sid, "question": "Укажите well_column для table_id"}).json()
    assert bad["ok"] is False and bad["error"] == "question_not_human"
    ok = client.post("/agent-tools/ask_engineer", json={"session_id": sid, "question": "Какие скважины посчитать?", "options": "Все из задачи; Только новые"}).json()
    assert ok["status"] == "needs_input"
    result = client.get(f"/sessions/{sid}/result").json()
    assert result["status"] == "needs_input"
    assert [o["label"] for o in result["requests"][0]["options"]] == ["Все из задачи", "Только новые"]


def test_long_job_returns_in_progress_then_finishes_through_activity(client: TestClient, agent: DemoAgent, activity_url: str) -> None:
    """Long agents: in_progress now, agent.progress lines while running, POST /cases/{id}/run source=agent at the end."""
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Запусти прогон для скважин 1601 и 1735")).json()
    sid = opened["session_id"]
    out = client.post("/agent-tools/start_long_job", json={"session_id": sid, "label": "Прогон демо-модели"}).json()
    assert out["status"] == "in_progress"
    fetched = client.get(f"/sessions/{sid}/result").json()
    assert fetched["status"] == "in_progress" and fetched["watch"]["kind"] == "timer" and fetched["watch"]["ref"] == sid
    assert human_text_problems(fetched["message"]) == []

    agent.jobs[sid].join(timeout=10)
    assert not agent.jobs[sid].is_alive()
    resume = [c for c in _FakeActivity.calls if c["path"].endswith("/run")]
    assert len(resume) == 1
    payload = resume[0]["json"]
    assert payload["action"] == "resume" and payload["source"] == "agent" and payload["agent_id"] == "demo_agent" and payload["task_id"] == "T-1"
    final = payload["agent_result"]
    assert final["status"] == "completed" and final["data"]["wells"] == ["1601", "1735"] and final["data"]["well_count"] == 2
    assert final["message"].startswith("Прогон демо-модели завершён: обработано 2 скважины")
    events = [c["json"] for c in _FakeActivity.calls if c["path"].endswith("/events")]
    # Engineer-facing lines: only agent.progress while the job runs, all in plain Russian.
    progress = [e for e in events if e["kind"] == "agent.progress"]
    assert progress and all(e["status"] == "waiting_agent" for e in progress)
    assert all(human_text_problems(e["status_message"]) == [] for e in progress)
    # Developer log: the router traces the tool call itself (hidden from the chat, shown in «Лог»).
    traces = [e for e in events if e["kind"] == "trace.tool"]
    assert [t["payload"]["tool"] for t in traces] == ["start_long_job"]
    assert traces[0]["payload"]["ok"] is True and traces[0]["payload"]["duration_ms"] >= 0 and traces[0]["payload"]["args"] == {"label": "Прогон демо-модели"}
    assert {e["kind"] for e in events} == {"agent.progress", "trace.tool"}
    # The session keeps the final result too (a poller can read it until the workflow closes the session).
    assert client.get(f"/sessions/{sid}/result").json()["status"] == "completed"


def test_open_session_without_objective_is_final(client: TestClient, activity_url: str) -> None:
    body = client.post("/agent-tools/open_session", json=task(activity_url, "")).json()
    assert body["ok"] is False and body["status"] == "needs_input"
    assert body["result"]["requests"][0]["question"].startswith("Опишите задачу")
