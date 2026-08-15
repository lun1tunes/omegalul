"""Unit + API + static asset tests for MAS Activity presentation service."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MAS_ACTIVITY_KEY", "dev-local")

from fastapi.testclient import TestClient

from app.enrich import BRIEF_TEMPLATES, build_brief, enrich_turn, format_duration, outcome_for
from app.main import app, reset_store

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
client = TestClient(app)
KEY = {"X-Activity-Key": "dev-local"}


def setup_function() -> None:
    reset_store()


def test_enrich_adds_brief_abs_time_duration_and_filters_secrets() -> None:
    turn = enrich_turn(
        {
            "at": "2026-08-15T01:02:03+00:00",
            "status": "EXCEL_EVIDENCE_READY",
            "summary": "Excel returned 3 fact(s).",
            "duration_ms": 12500,
            "from_role": "Excel Extractor",
            "to_role": "Schedule Builder",
            "details": {
                "fact_count": 3,
                "api_key": "should-not-appear",
                "source_snapshot_hash": "fnv1a32:abc",
            },
        }
    )
    assert turn["at_abs"] == "2026-08-15 06:02:03 Тюмень"
    assert turn["duration_label"] == "12.5 s"
    assert turn["outcome"] == "ok"
    assert "snapshot" in turn["brief"].lower() or "факт" in turn["brief"].lower()
    assert "Фактов в пакете: 3" in turn["brief"]
    assert all(c["id"] != "api_key" for c in turn["chips"])
    assert any(c["id"] == "fact_count" for c in turn["chips"])


def test_brief_templates_cover_core_statuses() -> None:
    for status in ("DELEGATED", "SCHEDULE_EVIDENCE_GAP", "STALLED_EVIDENCE_LOOP"):
        assert status in BRIEF_TEMPLATES
        brief = build_brief(status=status, summary="", brief=None, details={})
        assert 20 < len(brief) <= 800


def test_format_duration_and_outcome() -> None:
    assert format_duration(250) == "250 ms"
    assert format_duration(1500) == "1.5 s"
    assert format_duration(65000).startswith("1m")
    assert outcome_for("INVALID_SOURCE_FACTS_PACKET") == "block"
    assert outcome_for("SCHEDULE_EVIDENCE_GAP") == "wait"


def test_sync_preserves_presentation_fields() -> None:
    res = client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_present_1",
            "events": [
                {
                    "event_type": "handoff",
                    "at": "2026-08-15T10:11:12Z",
                    "status": "EXCEL_EVIDENCE_READY",
                    "summary": "Excel done",
                    "brief": "Excel Extractor закончил extract. Пакет фактов уходит в Schedule Builder.",
                    "duration_ms": 9400,
                    "handoff": {
                        "from_role": "Excel Extractor",
                        "to_role": "Schedule Builder",
                        "from_specialist": "excel_extraction_specialist",
                        "to_specialist": "schedule_builder_specialist",
                        "details": {"fact_count": 4},
                    },
                }
            ],
        },
    )
    assert res.status_code == 200
    turn = res.json()["turns"][0]
    assert turn["brief"].startswith("Excel Extractor")
    assert turn["at_abs"] == "2026-08-15 15:11:12 Тюмень"
    assert turn["duration_ms"] == 9400
    assert turn["duration_label"] == "9.4 s"

    feed = client.get("/v1/tasks/eng_present_1").json()
    assert feed["contract_version"] == "1.1"
    assert feed["activity"][0]["duration_label"] == "9.4 s"


def test_awaiting_accepts_uppercase_status_from_sync_gate_event() -> None:
    """Bug 1: AWAITING_HUMAN + human_gate must arm awaiting_human for the HITL composer."""
    gate = {
        "gate_id": "gate_upper_1",
        "kind": "needs_input",
        "reason": "Need WELLTRACK",
        "questions": [{"id": "q1", "text": "Attach WELLTRACK", "required": True}],
        "expected_version": 3,
    }
    res = client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_await_case",
            "events": [
                {
                    "event_type": "handoff",
                    "status": "AWAITING_HUMAN",
                    "summary": "Waiting on human",
                    "from_role": "Orchestrator",
                    "to_role": "Human",
                    "human_gate": gate,
                    "handoff": {
                        "from_role": "Orchestrator",
                        "to_role": "Human",
                        "from_specialist": "universal_orchestrator",
                        "to_specialist": "human_operator",
                    },
                }
            ],
        },
    )
    assert res.status_code == 200
    feed = client.get("/v1/tasks/eng_await_case").json()
    assert feed["status"] == "awaiting_human"
    assert feed["awaiting_human"] is True
    assert feed["human_gate"]["gate_id"] == "gate_upper_1"


def test_sync_handoff_does_not_clear_open_gate() -> None:
    """Bug 2: routine Trace Writer statuses must not drop an open gate or un-arm HITL."""
    seed = client.post("/v1/demo/seed", headers=KEY).json()
    task_id = seed["task_id"]
    before = client.get(f"/v1/tasks/{task_id}").json()
    assert before["awaiting_human"] is True
    gate_id = before["human_gate"]["gate_id"]

    res = client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": task_id,
            "events": [
                {
                    "event_type": "handoff",
                    "at": "2026-08-15T12:00:00Z",
                    "status": "EXCEL_EVIDENCE_READY",
                    "summary": "Excel returned facts",
                    "handoff": {
                        "from_role": "Excel Extractor",
                        "to_role": "Schedule Builder",
                        "from_specialist": "excel_extraction_specialist",
                        "to_specialist": "schedule_builder_specialist",
                        "details": {"fact_count": 2},
                    },
                }
            ],
        },
    )
    assert res.status_code == 200
    after = client.get(f"/v1/tasks/{task_id}").json()
    assert after["awaiting_human"] is True
    assert after["human_gate"]["gate_id"] == gate_id
    assert after["status"] == "awaiting_human"
    assert any(t.get("status") == "EXCEL_EVIDENCE_READY" for t in after["activity"])


def test_rejects_bad_task_id_and_oversized_declared_body() -> None:
    bad = client.post(
        "/v1/turns",
        headers=KEY,
        json={"task_id": "../etc/passwd", "turn": {"summary": "x", "from_role": "A", "to_role": "B"}},
    )
    assert bad.status_code == 422

    huge = client.post(
        "/v1/turns",
        headers={**KEY, "content-length": str(300_000)},
        json={"task_id": "ok", "turn": {"summary": "x", "from_role": "A", "to_role": "B"}},
    )
    assert huge.status_code == 413


def test_ready_health_and_static_assets() -> None:
    health = client.get("/health").json()
    assert health["version"] == "0.3.0"
    assert "hitl_backend" in health
    assert client.get("/ready").status_code == 200
    index = client.get("/")
    assert index.status_code == 200
    assert "composer" in index.text
    assert "taskRail" in index.text
    assert "cancelBtn" in index.text
    assert "taskSelect" not in index.text
    assert "openBtn" not in index.text
    js_text = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "brief" in js_text
    assert "duration_label" in js_text
    assert "submitHitl" in js_text
    assert "showFlash" in js_text
    assert "alert(" not in js_text
    assert "human_gate ?? data.gate" in js_text
    assert "at_abs" in js_text
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".brief" in css
    assert "outcome-ok" in css
    assert "--blue-900" in css
    assert ".flash" in css
    assert ".rail { display: none; }" not in css
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "duration_label" in js.text


def test_demo_seed_has_duration_brief_and_hitl_gate() -> None:
    res = client.post("/v1/demo/seed", headers=KEY)
    assert res.status_code == 200
    body = res.json()
    task_id = body["task_id"]
    assert body["awaiting_human"] is True
    assert body["human_gate"]["gate_id"]
    feed = client.get(f"/v1/tasks/{task_id}").json()
    activity = feed["activity"]
    assert len(activity) >= 6
    assert feed["awaiting_human"] is True
    assert feed["human_gate"]["kind"] == "needs_approval"
    assert all(item.get("brief") for item in activity)
    assert any(item.get("duration_ms") for item in activity)
    assert any(item.get("at_abs") for item in activity)
    assert any(item.get("status") == "AWAITING_HUMAN" for item in activity)


def test_local_hitl_approve_reply_reject(monkeypatch) -> None:
    monkeypatch.setenv("HITL_MODE", "local")
    seed = client.post("/v1/demo/seed", headers=KEY).json()
    task_id = seed["task_id"]

    bad = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={"action": "approve", "requested_by": "anonymous"},
    )
    assert bad.status_code == 400

    reply_missing = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={"action": "reply", "requested_by": "И. Иванов"},
    )
    assert reply_missing.status_code == 400

    reply = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={
            "action": "reply",
            "requested_by": "И. Иванов",
            "human_response": "Добавьте WCONPROD BHP=180 bar из того же snapshot.",
        },
    )
    assert reply.status_code == 200
    payload = reply.json()
    assert payload["ok"] is True
    assert payload["turn"]["status"] == "HUMAN_REPLY"
    assert payload["awaiting_human"] is False
    assert payload["orchestrator"]["status"] == "planning"

    seed2 = client.post("/v1/demo/seed", headers=KEY).json()
    approve = client.post(
        f"/v1/tasks/{seed2['task_id']}/hitl",
        headers=KEY,
        json={"action": "approve", "requested_by": "П. Петров"},
    )
    assert approve.status_code == 200
    assert approve.json()["orchestrator"]["status"] == "completed"
    assert approve.json()["turn"]["status"] == "HUMAN_APPROVED"

    seed3 = client.post("/v1/demo/seed", headers=KEY).json()
    reject = client.post(
        f"/v1/tasks/{seed3['task_id']}/hitl",
        headers=KEY,
        json={"action": "reject", "requested_by": "П. Петров", "human_response": "Scope слишком широкий"},
    )
    assert reject.status_code == 200
    assert reject.json()["orchestrator"]["status"] == "rejected"


def test_unauthorized_and_missing_task() -> None:
    assert client.post("/v1/demo/seed").status_code == 401
    assert client.get("/v1/tasks/missing_task_zzz").status_code == 404


def test_set_gate_sse_publishes_human_gate() -> None:
    import asyncio
    from app.main import _set_gate, _subscribers

    task_id = "sse_gate_field"
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subscribers[task_id].append(queue)

    async def run() -> dict:
        return await _set_gate(
            task_id,
            status="awaiting_human",
            version=42,
            gate={
                "gate_id": "g-sse-1",
                "kind": "needs_approval",
                "expected_version": 42,
                "reason": "SSE field check",
            },
        )

    snapshot = asyncio.run(run())
    assert snapshot["human_gate"]["gate_id"] == "g-sse-1"
    assert "gate" not in snapshot
    msg = queue.get_nowait()
    assert msg["type"] == "gate"
    assert msg["awaiting_human"] is True
    assert msg["human_gate"]["gate_id"] == "g-sse-1"
    assert "gate" not in msg
    _subscribers[task_id].remove(queue)


def test_live_hitl_status_failure_does_not_local_apply(monkeypatch) -> None:
    monkeypatch.setenv("HITL_MODE", "webhook")
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://example.invalid/orch")

    from app.orchestrator import OrchestratorError

    async def boom(_payload):
        raise OrchestratorError("orchestrator unreachable", status_code=502)

    monkeypatch.setattr("app.main.invoke_orchestrator", boom)

    seed = client.post("/v1/demo/seed", headers=KEY).json()
    task_id = seed["task_id"]
    before = client.get(f"/v1/tasks/{task_id}").json()
    assert before["awaiting_human"] is True

    res = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={"action": "approve", "requested_by": "И. Иванов", "gate_id": before["human_gate"]["gate_id"]},
    )
    assert res.status_code == 502
    detail = str(res.json()["detail"]).lower()
    assert "unreachable" in detail or "orchestrator" in detail

    after = client.get(f"/v1/tasks/{task_id}").json()
    assert after["awaiting_human"] is True
    assert after["human_gate"]["gate_id"] == before["human_gate"]["gate_id"]


def test_live_hitl_prefers_fresh_status_cas_over_body(monkeypatch) -> None:
    monkeypatch.setenv("HITL_MODE", "webhook")
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://example.invalid/orch")

    calls: list[dict] = []

    async def fake_invoke(payload):
        calls.append(dict(payload))
        if payload.get("action") == "status":
            return {
                "status": "awaiting_human",
                "version": 7,
                "human_gate": {
                    "gate_id": "fresh-gate",
                    "kind": "needs_approval",
                    "expected_version": 7,
                    "reason": "fresh",
                },
            }
        return {"status": "completed", "version": 8, "human_gate": None}

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)

    seed = client.post("/v1/demo/seed", headers=KEY).json()
    task_id = seed["task_id"]
    res = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={
            "action": "approve",
            "requested_by": "И. Иванов",
            "gate_id": "stale-gate",
            "expected_version": 1,
        },
    )
    assert res.status_code == 200
    assert len(calls) == 2
    assert calls[0]["action"] == "status"
    assert calls[1]["action"] == "approve"
    assert calls[1]["gate_id"] == "fresh-gate"
    assert calls[1]["expected_version"] == 7
