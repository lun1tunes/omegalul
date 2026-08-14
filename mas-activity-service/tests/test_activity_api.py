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
    assert turn["at_abs"] == "2026-08-15 01:02:03 UTC"
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
    assert turn["at_abs"] == "2026-08-15 10:11:12 UTC"
    assert turn["duration_ms"] == 9400
    assert turn["duration_label"] == "9.4 s"

    feed = client.get("/v1/tasks/eng_present_1").json()
    assert feed["contract_version"] == "1.1"
    assert feed["activity"][0]["duration_label"] == "9.4 s"


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
    assert client.get("/health").json()["version"] == "0.2.0"
    assert client.get("/ready").status_code == 200
    index = client.get("/")
    assert index.status_code == 200
    assert "brief" in (STATIC / "app.js").read_text(encoding="utf-8")
    assert "duration_label" in (STATIC / "app.js").read_text(encoding="utf-8")
    assert "at_abs" in (STATIC / "app.js").read_text(encoding="utf-8")
    assert ".brief" in (STATIC / "app.css").read_text(encoding="utf-8")
    assert "outcome-ok" in (STATIC / "app.css").read_text(encoding="utf-8")
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "duration_label" in js.text


def test_demo_seed_has_duration_and_brief() -> None:
    res = client.post("/v1/demo/seed", headers=KEY)
    assert res.status_code == 200
    task_id = res.json()["task_id"]
    activity = client.get(f"/v1/tasks/{task_id}").json()["activity"]
    assert len(activity) >= 5
    assert all(item.get("brief") for item in activity)
    assert any(item.get("duration_ms") for item in activity)
    assert any(item.get("at_abs") for item in activity)


def test_unauthorized_and_missing_task() -> None:
    assert client.post("/v1/demo/seed").status_code == 401
    assert client.get("/v1/tasks/missing_task_zzz").status_code == 404
