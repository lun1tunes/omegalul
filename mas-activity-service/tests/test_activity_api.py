"""Unit + API + static asset tests for MAS Activity presentation service."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.pop("DATABASE_URL", None)

from fastapi.testclient import TestClient
import pytest

from app.enrich import BRIEF_TEMPLATES, build_brief, enrich_turn, format_duration, outcome_for
from app.main import app, reset_store
from app.settings import VERSION

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
_TESTCLIENT_OPTIONS = (
    {"backend_options": {"use_uvloop": True}}
    if importlib.util.find_spec("uvloop")
    else {}
)
client = TestClient(app, **_TESTCLIENT_OPTIONS)
KEY = {}


@pytest.fixture(autouse=True)
def _isolate_activity_store(monkeypatch) -> None:
    """Reset memory and ignore host n8n/Activity URLs so unit tests stay offline."""
    for key in (
        "ACTIVITY_HYDRATE_URL",
        "ACTIVITY_LIST_URL",
        "ACTIVITY_FEED_URL",
        "ACTIVITY_STATE_PATH",
        "ACTIVITY_BINARIES_PATH",
        "ORCHESTRATOR_WEBHOOK_URL",
        "ORCHESTRATOR_AUTH_HEADER",
        "ORCHESTRATOR_AUTH_VALUE",
        "N8N_BASE_URL",
        "DATABASE_URL",
        "N8N_HOST",
        "N8N_USERNAME",
        "N8N_PASSWORD",
        "HITL_MODE",
        "MAS_ACTIVITY_KEY",
        "MAS_ACTIVITY_AUTH_DISABLED",
        "ACTIVITY_TLS_VERIFY",
        "ACTIVITY_CA_BUNDLE",
    ):
        monkeypatch.delenv(key, raising=False)
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
    assert turn["at_abs"] == "2026-08-15 06:02:03 UTC+5"
    assert turn["duration_label"] == "12.5 s"
    assert turn["outcome"] == "ok"
    assert turn["brief"].startswith(BRIEF_TEMPLATES["EXCEL_EVIDENCE_READY"])
    assert "Фактов в пакете: 3" in turn["brief"]
    assert turn["text"] == "Excel returned 3 fact(s)."
    assert all(c["id"] != "api_key" for c in turn["chips"])
    fact_chip = next(c for c in turn["chips"] if c["id"] == "fact_count")
    assert fact_chip["label"] == "Фактов"


def test_enrich_default_to_role_is_user() -> None:
    turn = enrich_turn({"status": "DELEGATED", "summary": "go"})
    assert turn["to"]["role"] == "User"
    assert turn["from"]["role"] == "Orchestrator"


def test_brief_templates_cover_core_statuses() -> None:
    for status in ("DELEGATED", "SCHEDULE_EVIDENCE_GAP", "STALLED_EVIDENCE_LOOP", "SCHEDULE_DRAFT_READY", "VERIFIED"):
        assert status in BRIEF_TEMPLATES
        brief = build_brief(status=status, summary="", brief=None, details={})
        assert 10 < len(brief) <= 800


def test_known_status_brief_wins_over_english_summary_and_brief() -> None:
    brief = build_brief(
        status="SCHEDULE_DRAFT_READY",
        summary="KEEP unlisted; shifted listed wells",
        brief="Timeline revise applied (8 shifts, keep)",
        details={},
    )
    assert brief == BRIEF_TEMPLATES["SCHEDULE_DRAFT_READY"]
    assert "KEEP" not in brief


def test_hitl_brief_uses_agent_russian_comment() -> None:
    brief = build_brief(
        status="NEEDS_APPROVAL",
        summary="Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.",
        brief="Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.",
        details={"error_code": "CASE_ERROR", "case_id": "eng_x"},
    )
    assert "утверждение" in brief
    assert "Критическая ошибка" not in brief
    assert outcome_for("NEEDS_APPROVAL") == "wait"
    assert outcome_for("RESULT_APPROVAL") == "wait"


def test_format_duration_and_outcome() -> None:
    assert format_duration(250) == "250 ms"
    assert format_duration(1500) == "1.5 s"
    assert format_duration(65000).startswith("1m")
    assert outcome_for("INVALID_SOURCE_FACTS_PACKET") == "block"
    assert outcome_for("SCHEDULE_EVIDENCE_GAP") == "wait"
    assert outcome_for("SCHEDULE_DRAFT_READY") == "ok"


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
    assert turn["brief"].startswith(BRIEF_TEMPLATES["EXCEL_EVIDENCE_READY"])
    assert "Фактов в пакете: 4" in turn["brief"]
    assert turn["at_abs"] == "2026-08-15 15:11:12 UTC+5"
    assert turn["duration_ms"] == 9400
    assert turn["duration_label"] == "9.4 s"

    feed = client.get("/v1/tasks/eng_present_1").json()
    assert feed["contract_version"] == "1.1"
    assert feed["activity"][0]["duration_label"] == "9.4 s"


def test_ingest_sync_stored_false_when_title_unchanged_no_turns() -> None:
    """No-turns meta path: stored mirrors actual persist, not mere presence of title/objective."""
    import asyncio

    from app.main import SyncPost, _ingest_sync, _tasks

    task_id = "act_meta_noop_1"

    async def _run() -> None:
        body = SyncPost(task_id=task_id, turns=[], events=[])
        first = await _ingest_sync(body, title="Same title", objective="Same objective")
        assert first["stored"] is True
        assert first["count"] == 0
        assert _tasks[task_id]["title"] == "Same title"

        second = await _ingest_sync(body, title="Same title", objective="Same objective")
        assert second["stored"] is False
        assert second["count"] == 0

        third = await _ingest_sync(body, title="Changed title", objective="Same objective")
        assert third["stored"] is True
        assert third["count"] == 0
        assert _tasks[task_id]["title"] == "Changed title"

    asyncio.run(_run())


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


def test_schedule_artifact_download_from_hydrate() -> None:
    body = "DATES\n  1 JAN 2025 /\n/\nWCONPROD\n  'W1' OPEN ORAT 10 * /\n/\n"
    res = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "act_sched_dl_1",
            "title": "Downloadable",
            "status": "completed",
            "filename": "FORECAST.INC",
            "generated_schedule": body,
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "act_sched_dl_1",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "VERIFIED",
                    "summary": "done",
                    "from_role": "Verifier",
                    "to_role": "Release",
                }
            ],
        },
    )
    assert res.status_code == 200
    feed = client.get("/v1/tasks/act_sched_dl_1").json()
    assert feed["schedule_artifact"]["available"] is True
    assert feed["schedule_artifact"]["filename"] == "FORECAST.INC"
    assert feed["schedule_artifact"]["download_path"] == "/v1/tasks/act_sched_dl_1/schedule"
    assert feed["schedule_artifact"]["byte_length"] == len(body.encode("utf-8"))
    # Full text must not bloat the feed JSON.
    assert "DATES" not in json.dumps(feed["schedule_artifact"])

    dl = client.get("/v1/tasks/act_sched_dl_1/schedule")
    assert dl.status_code == 200
    assert dl.text == body
    assert "attachment" in dl.headers.get("content-disposition", "")
    assert "FORECAST.INC" in dl.headers.get("content-disposition", "")

    missing = client.get("/v1/tasks/act_no_sched/schedule")
    assert missing.status_code == 404


def test_semantic_diff_from_hydrate_and_demo() -> None:
    res = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "act_sem_diff_1",
            "title": "Diff",
            "status": "awaiting_human",
            "compact_data": {
                "semantic_diff": {
                    "changed_keywords": ["WELOPEN", "WCONPROD"],
                    "commissioning_wells": ["P1"],
                    "edits": [
                        {"keyword": "WELOPEN", "well": "P1", "operation": "move", "summary": "1 JAN → 1 MAR"},
                    ],
                    "include_graph_changed": True,
                }
            },
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "act_sem_diff_1",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "AWAITING_HUMAN",
                    "summary": "review",
                    "from_role": "Orchestrator",
                    "to_role": "Human",
                }
            ],
        },
    )
    assert res.status_code == 200
    assert res.json().get("feed", {}).get("semantic_diff") is True
    feed = client.get("/v1/tasks/act_sem_diff_1").json()
    diff = feed["semantic_diff"]
    assert diff["changed_keywords"] == ["WELOPEN", "WCONPROD"]
    assert diff["commissioning_wells"] == ["P1"]
    assert diff["edits"][0]["summary"] == "1 JAN → 1 MAR"
    assert diff["include_graph_changed"] is True
    assert "updated_at" in diff

    seed = client.post("/v1/demo/seed", headers=KEY).json()
    assert seed["semantic_diff"]["changed_keywords"]
    demo_feed = client.get(f"/v1/tasks/{seed['task_id']}").json()
    assert demo_feed["semantic_diff"]["edits"]


def test_semantic_diff_include_graph_only() -> None:
    """INCLUDE-graph-only diffs must survive public shaping for the expander."""
    from app.main import _semantic_diff_public

    assert _semantic_diff_public({"include_graph_changed": True}) == {"include_graph_changed": True}
    assert _semantic_diff_public({"include_graph_changed": False}) is None
    assert _semantic_diff_public({}) is None

    res = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "act_sem_diff_include_only",
            "title": "Include only",
            "status": "awaiting_human",
            "compact_data": {"semantic_diff": {"include_graph_changed": True}},
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "act_sem_diff_include_only",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "AWAITING_HUMAN",
                    "summary": "review",
                    "from_role": "Orchestrator",
                    "to_role": "Human",
                }
            ],
        },
    )
    assert res.status_code == 200
    assert res.json().get("feed", {}).get("semantic_diff") is True
    feed = client.get("/v1/tasks/act_sem_diff_include_only").json()
    assert feed["semantic_diff"]["include_graph_changed"] is True
    assert feed["semantic_diff"].get("summary") in (None, "")
    assert "changed_keywords" not in feed["semantic_diff"]


def test_maybe_capture_schedule_null_output_package() -> None:
    """merge_result.output_package: null must not AttributeError during capture."""
    from app.main import _maybe_capture_schedule, _new_task_shell

    body = "DATES\n  1 JAN 2025 /\n/\n"
    task = _new_task_shell("act_null_pkg")
    # extract_schedule_from_orchestrator misses merge_result; for-loop must still store text.
    ok = _maybe_capture_schedule(
        task,
        {
            "compact_data": {
                "merge_result": {
                    "output_package": None,
                    "generated_schedule": body,
                }
            }
        },
    )
    assert ok is True
    assert task["schedule_artifact"]["text"] == body

    # Top-level still wins when extract finds it, even with a null package sibling.
    task2 = _new_task_shell("act_null_pkg2")
    ok2 = _maybe_capture_schedule(
        task2,
        {
            "filename": "TOP.INC",
            "generated_schedule": body,
            "compact_data": {
                "merge_result": {
                    "output_package": None,
                    "generated_schedule": "OTHER",
                }
            },
        },
    )
    assert ok2 is True
    assert task2["schedule_artifact"]["filename"] == "TOP.INC"


def test_extract_schedule_from_builder_deliverables() -> None:
    """Draft/review Builder result stores full .INC in deliverables[], not compact preview."""
    from app.commissioning import extract_schedule_from_orchestrator
    from app.main import _maybe_capture_schedule, _new_task_shell

    full = "DATES\n  1 JAN 2025 /\n/\n" + ("WCONPROD\n  'W1' OPEN ORAT 1 * /\n/\n\n" * 400)
    preview = full[:4000]
    assert len(full) > len(preview)

    orch = {
        "result": {
            "contract": "specialist_result",
            "status": "needs_approval",
            "deliverables": [
                {
                    "kind": "schedule_inc_text",
                    "filename": "FORECAST.INC",
                    "description": "Validated SCHEDULE text",
                    "schedule_text": full,
                }
            ],
            "compact_data": {
                "release_ready": True,
                "generated_schedule_bytes": len(full),
                "generated_schedule_preview": preview,
                "merge_result": {"output_package": {"root_path": "FORECAST.INC"}},
            },
        }
    }
    extracted = extract_schedule_from_orchestrator(orch)
    assert extracted is not None
    assert extracted[0] == "FORECAST.INC"
    assert extracted[1] == full
    assert extracted[1] != preview

    task = _new_task_shell("act_deliv_sched")
    assert _maybe_capture_schedule(task, orch) is True
    assert task["schedule_artifact"]["text"] == full
    assert task["schedule_artifact"]["filename"] == "FORECAST.INC"

    # Bare specialist_result (no wrapping result) also works.
    bare = orch["result"]
    got_bare = extract_schedule_from_orchestrator(bare)
    assert got_bare is not None and got_bare[1] == full


def test_ready_health_and_static_assets() -> None:
    health = client.get("/health").json()
    assert health["version"] == VERSION
    assert health["n8n_transport"] == "unconfigured"
    assert "state_persist" in health
    assert "auth_required" not in health
    ready = client.get("/ready")
    assert ready.status_code == 503
    detail = ready.json()["detail"]
    assert detail["ready"] is False
    assert "ORCHESTRATOR_WEBHOOK_URL" in " ".join(detail.get("missing_config") or [])
    index = client.get("/")
    assert index.status_code == 200
    assert "composer" in index.text
    assert "startComposer" in index.text
    assert "taskName" in index.text
    assert "Название задачи" in index.text
    assert "newTaskBtn" in index.text
    assert "rail-new-task" in index.text
    assert "railList" in index.text
    assert "brandHome" in index.text
    assert "NOVATEK RE MASter" in index.text
    assert "Workspace" in index.text
    assert "Novatek STC reservoir engineering multi-agent system" in index.text
    assert "reloadDurableBtn" not in index.text
    assert "Из Data Tables" not in index.text
    assert "requestPanel" in index.text
    assert "notFound" in index.text
    assert "Задача не найдена" in index.text
    assert "Вернуться на главную" in index.text
    assert "taskRail" in index.text
    assert "id=\"cancelBtn\"" not in index.text
    assert "id=\"approveBtn\"" not in index.text
    assert "id=\"rejectBtn\"" not in index.text
    assert "id=\"replyBtn\"" in index.text
    assert "hitlDropzone" in index.text
    assert "Утвердить" not in index.text
    assert "Отклонить" not in index.text
    assert "taskSelect" not in index.text
    assert "openBtn" not in index.text
    js_text = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "X-Activity-Key" not in js_text
    assert "mas_activity_key" not in js_text
    assert "brief" in js_text
    assert "duration_label" in js_text
    assert "submitHitl" in js_text
    assert "humanizeGateReason" in js_text
    assert "humanizeQuestion" in js_text
    assert "looksMachineAsk" in js_text
    assert 'q.required ? "обязательно"' not in js_text
    assert "формат: ${q.expected_format}" not in js_text
    assert "app.js?v=82" in index.text
    assert "schema.js?v=6" in index.text
    assert "app.css?v=75" in index.text
    assert "viewChatBtn" in index.text
    assert "viewSchemaBtn" in index.text
    assert ">Чат<" in index.text
    assert ">Схема<" in index.text
    assert "schemaView" in index.text
    assert "schemaTimeline" in index.text
    assert "Постановка задачи" in index.text
    assert "Результат" in index.text
    assert "Нет такой задачи в Workspace." in index.text
    assert "Data Tables" not in index.text
    assert "submitStart" in js_text
    assert "form.append(\"task_name\"" in js_text
    assert "catalogTaskName" in js_text
    assert "beginRenameTask" in js_text
    assert 'method: "PATCH"' in js_text
    assert "renameTaskBtn" in index.text
    assert "clearWorkspaceView" in js_text
    assert "startResumeTask" in js_text
    assert "turnKicker" in js_text
    assert '"Сообщение"' in js_text
    assert "displayRole" in js_text
    assert 'User: "Вы"' in js_text
    assert "syncScheduleRootField" in js_text
    assert "hydrateFromDataTables" in js_text
    assert "let currentTask = null" in js_text
    assert "reloadFeed && currentTask" in js_text
    assert "JSON.stringify({\n          question_id:" not in js_text
    assert "refreshRail({ durable: !snap.ok })" in js_text
    assert "setTaskHeader" in js_text
    assert "attachLive" in js_text
    assert "pollFeed" in js_text
    assert "feedMatchesOpenTask" in js_text
    assert "bumpFeedGeneration" in js_text
    assert "composing: true" in js_text
    assert "applyFeedMeta(msg)" in js_text
    assert "data.skipped" in js_text
    assert "Перезапуск не выполнен" in js_text
    assert "setWorkspaceView" in js_text
    assert "syncSchema" in js_text
    assert "MasSchema" in js_text
    schema_js = (STATIC / "schema.js").read_text(encoding="utf-8")
    assert "function buildSchemaFrames" in schema_js
    assert "handoff_message" in schema_js
    assert "DRAW_EDGE_KEYS" in schema_js
    assert "pairVisual" in schema_js
    assert "dRev" in schema_js
    assert "excel_orch" in schema_js
    assert "schema-slip" in (STATIC / "app.css").read_text(encoding="utf-8")
    assert "schema-status" in (STATIC / "app.css").read_text(encoding="utf-8")
    assert "schema-caption" in (STATIC / "app.css").read_text(encoding="utf-8")
    assert "markerUnits=\"userSpaceOnUse\"" in schema_js
    assert "pathMidpoint" in schema_js
    assert "getTotalLength" in schema_js
    assert "getPointAtLength" in schema_js
    assert "getScreenCTM" in schema_js
    assert "cubicArcMid" in schema_js
    assert "translate(-50%, calc(-100% - 8px))" in (STATIC / "app.css").read_text(encoding="utf-8")
    assert "statusLabel" in schema_js
    assert "setCaption" in schema_js
    assert "Ожидает задачу" in schema_js
    assert "is-live-in" in (STATIC / "app.css").read_text(encoding="utf-8")
    assert "animateSlips" in schema_js
    assert "liveNewStep" in schema_js
    assert "showLoadError" in js_text
    assert "showNotFound(taskId)" in js_text
    # Non-404 / network failures must not claim the task is missing from Activity+DT.
    assert "showNotFound(taskId);\n        showFlash" not in js_text
    assert "showLoadError(taskId, `Не удалось загрузить задачу (${snap.status}).`)" in js_text
    assert "showLoadError(taskId, \"Сеть недоступна при загрузке задачи.\")" in js_text
    assert "/cases" in js_text
    assert "submitStart" in js_text
    assert 'id="scheduleDownload"' not in index.text
    assert 'id="scheduleDownloadHead"' in index.text
    assert 'user: "Вы"' in js_text
    assert "who-track" not in js_text
    assert "who-line" not in js_text
    assert 'arrow.className = "arrow"' in js_text
    assert "lane_dir" in js_text
    assert "paintScheduleDownloadHead" in js_text
    assert "appendScheduleDownload" not in js_text
    assert "Скачать" in js_text
    assert "schedule_artifact: data.schedule_artifact" in js_text
    assert "semantic_diff: data.semantic_diff" in js_text
    assert "renderSemanticDiff" in js_text
    assert "diffExpander" in index.text
    assert "Изменения между версиями" in index.text
    assert "li._masTurn = turn" in js_text
    assert "statusDot" in index.text
    assert "titleText" in index.text
    assert "backendLabel" not in index.text
    assert "liveLabel" not in index.text
    assert "statusTone" in js_text
    css_text = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "task-line" in css_text
    assert "tone-hitl" in css_text
    assert ".transcript[hidden]" in css_text
    assert ".workspace.mode-schema .chat-pane" in css_text
    assert "tone-error" in css_text
    assert "who-track" not in css_text
    assert "who-line" not in css_text
    assert ".who .arrow::after" in css_text
    assert "schedule-download-row" not in css_text
    assert ".transcript-head .schedule-download" in css_text
    assert "diff-expander" in css_text
    assert "grid-template-columns: 280px 1fr" in css_text
    assert "showFlash" in js_text
    assert "alert(" not in js_text
    assert "human_gate ?? data.gate" in js_text
    assert "at_abs" in js_text
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert ".brief" in css
    assert ".dropzone" in css
    assert "outcome-ok" in css
    assert "--blue-900" in css
    assert ".flash" in css
    assert ".rail { display: none; }" not in css
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "duration_label" in js.text


def test_cors_preflight_and_get_allow_any_origin() -> None:
    origin = "http://localhost:4173"
    preflight = client.options(
        "/v1/tasks",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code in {200, 204}
    assert preflight.headers.get("access-control-allow-origin") == "*"
    assert preflight.headers.get("access-control-allow-credentials") != "true"
    health = client.get("/health", headers={"Origin": origin})
    assert health.status_code == 200
    assert health.headers.get("access-control-allow-origin") == "*"
    assert health.headers.get("access-control-allow-credentials") != "true"


def test_diagnostics_without_n8n_is_degraded() -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["n8n_transport"] == "unconfigured"

    diagnostics = client.get("/v1/diagnostics/connectivity")
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert body["ready"] is False
    assert body["status"] == "degraded"
    assert body["data_tables"]["configured"] is False
    assert body["orchestrator"]["ok"] is False


def test_start_fails_fast_when_n8n_unconfigured() -> None:
    res = client.post(
        "/v1/tasks/start",
        data={"task_description": "REVISE dates", "requested_by": "И. Иванов"},
    )
    assert res.status_code == 503
    assert "n8n" in str(res.json()["detail"]).lower() or "ORCHESTRATOR" in str(res.json()["detail"])


def test_start_task_with_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        return {
            "contract": "orchestrator_response",
            "task_id": payload["task_id"],
            "version": 1,
            "status": "awaiting_human",
            "human_gate": {
                "gate_id": "gate_start_files",
                "kind": "needs_input",
                "expected_version": 1,
            },
            "message": "started",
        }

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    schedule = tmp_path / "schedule.inc"
    schedule.write_text("DATES\n1 JAN 2020 /\n/\n", encoding="utf-8")
    excel = tmp_path / "dates.xlsx"
    excel.write_bytes(b"PK\x03\x04fake-xlsx")

    res = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={
            "task_description": "REVISE даты ввода по Excel",
            "requested_by": "И. Иванов",
            "schedule_root": "schedule.inc",
        },
        files=[
            ("schedule_files", ("schedule.inc", schedule.read_bytes(), "text/plain")),
            ("file", ("dates.xlsx", excel.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["orchestrator_pending"] is True
    assert body["backend"] == "webhook"
    assert body["file_count"] == 2
    assert body["awaiting_human"] is False
    assert body["human_gate"] is None
    task_id = body["task_id"]
    assert str(task_id).startswith("act_")
    feed = client.get(f"/v1/tasks/{task_id}").json()
    assert feed["title"].startswith("REVISE")
    assert feed["objective"] == "REVISE даты ввода по Excel"
    assert feed["attached_files"] == ["dates.xlsx", "schedule.inc"]
    assert feed["activity"][0]["status"] == "TASK_STARTED"
    assert feed["activity"][0]["details"]["objective"] == "REVISE даты ввода по Excel"
    assert feed["activity"][0]["details"]["files"] == ["dates.xlsx", "schedule.inc"]
    assert sum(1 for t in feed["activity"] if t.get("status") in {"TASK_STARTED", "ORCH_DISPATCHED"}) == 1
    assert not any(t.get("status") == "ORCH_DISPATCHED" for t in feed["activity"])
    assert feed["awaiting_human"] is True
    assert feed["human_gate"]["gate_id"]


def test_start_requires_named_engineer() -> None:
    res = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "x", "requested_by": "anonymous"},
    )
    assert res.status_code == 400


def test_live_start_forwards_files(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")
    captured: dict = {}

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        captured["payload"] = payload
        captured["files"] = files
        return {
            "contract": "orchestrator_response",
            "task_id": "eng_live_1",
            "version": 1,
            "status": "planning",
            "human_gate": None,
            "message": "started",
        }

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    res = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "CREATE schedule", "requested_by": "П. Петров"},
        files=[("schedule_files", ("root.inc", b"WELSPECS\n/", "text/plain"))],
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert captured["payload"]["action"] == "start"
    assert captured["payload"]["request"]["build_mode"] == "AUTO"
    assert "schedule_files" in captured["files"]
    assert str(body["task_id"]).startswith("act_")
    assert captured["payload"]["task_id"] == body["task_id"]
    assert body["accepted"] is True
    assert body["awaiting_human"] is False
    feed = client.get(f"/v1/tasks/{body['task_id']}").json()
    assert feed["status"] == "planning"


def test_sync_handoffs_follow_orch_alias_to_act_task(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        return {
            "contract": "orchestrator_response",
            "task_id": "eng_alias_1",
            "version": 1,
            "status": "planning",
            "human_gate": None,
            "message": "started",
        }

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    started = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "CREATE schedule", "requested_by": "П. Петров"},
    )
    assert started.status_code == 200, started.text
    act_id = started.json()["task_id"]

    sync = client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_alias_1",
            "events": [
                {
                    "event_type": "handoff",
                    "at": "2026-08-18T12:00:00Z",
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
    assert sync.status_code == 200, sync.text
    assert sync.json()["task_id"] == act_id
    feed = client.get(f"/v1/tasks/{act_id}").json()
    assert any(t.get("status") == "EXCEL_EVIDENCE_READY" for t in feed["activity"])
    alias = client.get("/v1/tasks/eng_alias_1")
    assert alias.status_code == 200
    assert alias.json()["task_id"] == act_id
    rail_ids = [row["task_id"] for row in client.get("/v1/tasks").json()["tasks"]]
    assert act_id in rail_ids
    assert "eng_alias_1" not in rail_ids


def test_sync_before_start_bind_merges_into_act_task(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        return {
            "task_id": "eng_early_1",
            "version": 1,
            "status": "planning",
            "human_gate": None,
        }

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    early = client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_early_1",
            "events": [
                {
                    "event_type": "handoff",
                    "at": "2026-08-18T11:00:00Z",
                    "status": "DELEGATED",
                    "summary": "Planner delegated Excel",
                    "handoff": {
                        "from_role": "Orchestrator",
                        "to_role": "Excel Extractor",
                    },
                }
            ],
        },
    )
    assert early.status_code == 200
    started = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "CREATE schedule", "requested_by": "П. Петров"},
    )
    act_id = started.json()["task_id"]
    feed = client.get(f"/v1/tasks/{act_id}").json()
    assert any(t.get("status") == "DELEGATED" for t in feed["activity"])
    assert any(t.get("status") == "TASK_STARTED" for t in feed["activity"])
    rail_ids = [row["task_id"] for row in client.get("/v1/tasks").json()["tasks"]]
    assert "eng_early_1" not in rail_ids


def test_sync_does_not_reopen_completed_task() -> None:
    hydrate = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_done_1",
            "title": "Done",
            "status": "completed",
            "events": [
                {
                    "event_type": "handoff",
                    "at": "2026-08-18T10:00:00Z",
                    "status": "VERIFIED",
                    "summary": "done",
                    "handoff": {"from_role": "Verifier", "to_role": "Release"},
                }
            ],
        },
    )
    assert hydrate.status_code == 200, hydrate.text
    before = client.get("/v1/tasks/eng_done_1").json()
    assert before["status"] == "completed"
    sync = client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_done_1",
            "events": [
                {
                    "event_type": "handoff",
                    "at": "2026-08-18T10:05:00Z",
                    "status": "EXCEL_EVIDENCE_READY",
                    "summary": "late handoff",
                    "handoff": {
                        "from_role": "Excel Extractor",
                        "to_role": "Schedule Builder",
                    },
                }
            ],
        },
    )
    assert sync.status_code == 200
    after = client.get("/v1/tasks/eng_done_1").json()
    assert after["status"] == "completed"
    assert any(t.get("status") == "EXCEL_EVIDENCE_READY" for t in after["activity"])


def test_start_empty_orchestrator_payload_records_failure(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        return {}

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    res = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "CREATE schedule", "requested_by": "П. Петров"},
    )
    assert res.status_code == 200, res.text
    feed = client.get(f"/v1/tasks/{res.json()['task_id']}").json()
    assert feed["status"] == "error"
    assert any(t.get("status") == "ORCH_FAILED" for t in feed["activity"])
    assert feed["awaiting_human"] is False


def test_hitl_409_does_not_record_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        if payload.get("action") == "status":
            return {"status": "planning", "version": 2, "human_gate": None}
        raise AssertionError("resume must not run when status is not awaiting")

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    seed = client.post("/v1/demo/seed", headers=KEY).json()
    task_id = seed["task_id"]
    before = client.get(f"/v1/tasks/{task_id}").json()
    res = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={"action": "approve", "requested_by": "И. Иванов", "gate_id": before["human_gate"]["gate_id"]},
    )
    assert res.status_code == 409
    after = client.get(f"/v1/tasks/{task_id}").json()
    assert not any(t.get("status") == "HUMAN_APPROVED" for t in after["activity"])
    assert not any(t.get("status") == "ORCH_DISPATCHED" for t in after["activity"])


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
    assert feed["semantic_diff"]["changed_keywords"]
    assert body["semantic_diff"]["edits"]


def test_hitl_approve_reply_reject(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        action = payload.get("action")
        if action == "status":
            return {
                "status": "awaiting_human",
                "version": 1,
                "human_gate": {
                    "gate_id": "g-seed",
                    "kind": "needs_approval",
                    "expected_version": 1,
                },
            }
        if action == "reply":
            return {"status": "planning", "version": 2, "human_gate": None}
        if action == "approve":
            return {"status": "completed", "version": 2, "human_gate": None}
        if action == "reject":
            return {"status": "rejected", "version": 2, "human_gate": None}
        return {"status": "planning", "version": 2, "human_gate": None}

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
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


def test_missing_task() -> None:
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


def test_set_gate_clears_stale_status_message_on_status_refresh() -> None:
    import asyncio
    from app.main import _set_gate, _tasks

    task_id = "stale_status_msg"

    async def conflict_then_running() -> tuple[dict, dict]:
        first = await _set_gate(
            task_id,
            status="conflict",
            message="Missing INCLUDE bodies",
        )
        second = await _set_gate(task_id, status="running")
        return first, second

    first, second = asyncio.run(conflict_then_running())
    assert first["status_message"] == "Missing INCLUDE bodies"
    assert second["status"] == "running"
    assert second["status_message"] is None
    assert _tasks[task_id].get("status_message") is None


def test_set_gate_noop_does_not_republish() -> None:
    import asyncio
    from app.main import _set_gate, _subscribers

    task_id = "sse_gate_noop"
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subscribers[task_id].append(queue)

    async def run() -> None:
        await _set_gate(task_id, status="running", version=1)
        await _set_gate(task_id, status="running", version=1)

    asyncio.run(run())
    assert queue.get_nowait()["type"] == "gate"
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()
    _subscribers[task_id].remove(queue)


def test_task_attached_files_accepts_either_start_signal() -> None:
    from app.main import _task_attached_files

    by_status = {
        "attached_files": [],
        "turns": [
            {
                "status": "TASK_STARTED",
                "details": {"files": ["a.inc"]},
            }
        ],
    }
    by_action = {
        "attached_files": [],
        "turns": [
            {
                "status": "ORCH_CONFLICT",
                "details": {"action": "start", "files": ["b.inc"]},
            }
        ],
    }
    specialist = {
        "attached_files": [],
        "turns": [
            {
                "status": "EXCEL_EVIDENCE_READY",
                "details": {"files": ["not-start.xlsx"]},
            }
        ],
    }
    assert _task_attached_files(by_status) == ["a.inc"]
    assert _task_attached_files(by_action) == ["b.inc"]
    assert _task_attached_files(specialist) == []


def test_n8n_rest_failed_execution_never_returns_parsed_response(monkeypatch) -> None:
    """Bugbot: error/crashed/canceled must fail closed even if orch JSON is parseable."""
    import asyncio
    from app.orchestrator import OrchestratorError, _invoke_n8n_rest

    class FakeResp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            return FakeResp(200, {"data": {"executionId": "exec-fail-1"}})

        async def get(self, url, params=None):
            return FakeResp(
                200,
                {
                    "data": {
                        "status": "error",
                        "data": {
                            "resultData": {
                                "runData": {
                                    "Format orchestrator response": [
                                        {
                                            "data": {
                                                "main": [
                                                    [
                                                        {
                                                            "json": {
                                                                "orchestrator_response": {
                                                                    "status": "ok",
                                                                    "task_id": "should-not-leak",
                                                                    "message": "parsed but run failed",
                                                                }
                                                            }
                                                        }
                                                    ]
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        },
                    }
                },
            )

    monkeypatch.setenv("N8N_BASE_URL", "http://n8n.test")
    monkeypatch.setenv("N8N_USERNAME", "u")
    monkeypatch.setenv("N8N_PASSWORD", "p")
    monkeypatch.setenv("ORCHESTRATOR_WORKFLOW_ID", "wf-test")
    monkeypatch.setattr("app.orchestrator.httpx.AsyncClient", FakeClient)

    async def _login_ok(client, cfg):
        return None

    monkeypatch.setattr("app.orchestrator._login", _login_ok)

    async def run():
        return await _invoke_n8n_rest({"action": "status", "task_id": "t1"}, timeout_s=5)

    with pytest.raises(OrchestratorError) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 502
    assert "should-not-leak" not in str(exc.value)


def test_static_ui_requires_schedule_root_and_generic_conflict_banner() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Выберите корневой schedule" in js
    assert "schedules.length >= 2 && !root" in js
    assert "полным пакетом schedule или без лишних INCLUDE" not in js
    assert "в ленте выше обычно есть причина" in js
    assert "startSubmitBtn.disabled = true" in js
    assert 'startSubmitBtn.setAttribute("aria-busy", "true")' in js
    assert "formatStartError" in js
    assert "emptyFeedMessage" in js
    assert "hitlDropzone" in js
    assert "Нужен текст ответа или вложение." in js


def test_live_hitl_status_failure_does_not_local_apply(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://example.invalid/orch")

    from app.orchestrator import OrchestratorError

    async def boom(_payload, *, files=None, timeout_s=90.0):
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
    assert not any(t.get("status") == "HUMAN_APPROVED" for t in after["activity"])
    assert not any(t.get("status") == "ORCH_DISPATCHED" for t in after["activity"])
    assert any(t.get("status") == "ORCH_FAILED" for t in after["activity"])


def test_live_hitl_prefers_fresh_status_cas_over_body(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://example.invalid/orch")

    calls: list[dict] = []

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        calls.append({"payload": dict(payload), "files": files})
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
    assert calls[0]["payload"]["action"] == "status"
    assert calls[1]["payload"]["action"] == "approve"
    assert calls[1]["payload"]["gate_id"] == "fresh-gate"
    assert calls[1]["payload"]["expected_version"] == 7


def test_live_hitl_reattaches_start_binaries(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")
    monkeypatch.setenv("ACTIVITY_BINARIES_PATH", str(tmp_path / "bins"))

    calls: list[dict] = []

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        calls.append({
            "action": payload.get("action"),
            "files": files,
            "task_id": payload.get("task_id"),
            "hitl_new_attachments": payload.get("hitl_new_attachments"),
        })
        if payload.get("action") == "start":
            return {
                "task_id": "eng_bin_1",
                "version": 1,
                "status": "awaiting_human",
                "human_gate": {
                    "gate_id": "g1",
                    "kind": "needs_input",
                    "expected_version": 1,
                    "questions": [
                        {
                            "id": "column_selection",
                            "question": "Which column(s)? (Скважина; Дата ввода)",
                        }
                    ],
                },
                "message": "need columns",
            }
        if payload.get("action") == "status":
            return {
                "status": "awaiting_human",
                "version": 1,
                "human_gate": {
                    "gate_id": "g1",
                    "kind": "needs_input",
                    "expected_version": 1,
                },
            }
        return {"status": "planning", "version": 2, "human_gate": None}

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)

    started = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "REVISE dates", "requested_by": "П. Петров"},
        files=[
            ("file", ("dates.xlsx", b"PK\x03\x04xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("schedule_files", ("root.inc", b"DATES\n/", "text/plain")),
        ],
    )
    assert started.status_code == 200, started.text
    task_id = started.json()["task_id"]
    assert str(task_id).startswith("act_")
    start_calls = [c for c in calls if c["action"] == "start"]
    assert start_calls and start_calls[0]["task_id"] == task_id

    res = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        json={"action": "reply", "requested_by": "П. Петров", "human_response": "Скважина; Дата ввода"},
    )
    assert res.status_code == 200, res.text
    resume_calls = [c for c in calls if c["action"] == "reply"]
    assert resume_calls
    assert resume_calls[0]["task_id"] == "eng_bin_1"
    files = resume_calls[0]["files"] or {}
    assert "file" in files
    assert files["file"][0] == "dates.xlsx"
    assert files["file"][1].startswith(b"PK")
    assert any(k.startswith("schedule_file") for k in files)
    assert str(resume_calls[0]["hitl_new_attachments"]).lower() == "false"


def test_hitl_multipart_adds_schedule_file_to_resume(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")
    monkeypatch.setenv("ACTIVITY_BINARIES_PATH", str(tmp_path / "bins"))
    calls: list[dict] = []

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        calls.append({
            "action": payload.get("action"),
            "files": files,
            "task_id": payload.get("task_id"),
            "hitl_new_attachments": payload.get("hitl_new_attachments"),
        })
        if payload.get("action") == "start":
            return {
                "task_id": "eng_hitl_file_1",
                "version": 1,
                "status": "awaiting_human",
                "human_gate": {
                    "gate_id": "g_inc",
                    "kind": "needs_input",
                    "expected_version": 1,
                },
            }
        if payload.get("action") == "status":
            return {
                "status": "awaiting_human",
                "version": 1,
                "human_gate": {
                    "gate_id": "g_inc",
                    "kind": "needs_input",
                    "expected_version": 1,
                },
            }
        return {"status": "planning", "version": 2, "human_gate": None}

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    started = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "REVISE dates", "requested_by": "П. Петров"},
        files=[
            ("file", ("dates.xlsx", b"PK\x03\x04xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("schedule_files", ("root.inc", b"DATES\n/", "text/plain")),
        ],
    )
    task_id = started.json()["task_id"]
    res = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        data={
            "action": "reply",
            "requested_by": "П. Петров",
            "human_response": "Добавлен недостающий INCLUDE.",
        },
        files=[("schedule_files", ("WELLS.INC", b"WELSPECS\n/", "text/plain"))],
    )
    assert res.status_code == 200, res.text
    reply = [c for c in calls if c["action"] == "reply"][0]
    files = reply["files"] or {}
    assert files["file"][0] == "dates.xlsx"
    names = {v[0] for v in files.values()}
    assert "root.inc" in names
    assert "WELLS.INC" in names
    assert str(reply["hitl_new_attachments"]).lower() == "true"


def test_hitl_multipart_files_only_reply(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://orch.test/hook")
    monkeypatch.setenv("ACTIVITY_BINARIES_PATH", str(tmp_path / "bins"))
    calls: list[dict] = []

    async def fake_invoke(payload, *, files=None, timeout_s=90.0):
        calls.append({
            "action": payload.get("action"),
            "files": files,
            "hitl_new_attachments": payload.get("hitl_new_attachments"),
        })
        if payload.get("action") == "start":
            return {
                "task_id": "eng_hitl_file_2",
                "version": 1,
                "status": "awaiting_human",
                "human_gate": {
                    "gate_id": "g_inc2",
                    "kind": "needs_input",
                    "expected_version": 1,
                },
            }
        if payload.get("action") == "status":
            return {
                "status": "awaiting_human",
                "version": 1,
                "human_gate": {
                    "gate_id": "g_inc2",
                    "kind": "needs_input",
                    "expected_version": 1,
                },
            }
        return {"status": "planning", "version": 2, "human_gate": None}

    monkeypatch.setattr("app.main.invoke_orchestrator", fake_invoke)
    started = client.post(
        "/v1/tasks/start",
        headers=KEY,
        data={"task_description": "REVISE dates", "requested_by": "П. Петров"},
        files=[("schedule_files", ("root.inc", b"DATES\n/", "text/plain"))],
    )
    task_id = started.json()["task_id"]
    res = client.post(
        f"/v1/tasks/{task_id}/hitl",
        headers=KEY,
        data={"action": "reply", "requested_by": "П. Петров"},
        files=[("schedule_files", ("WELLS.INC", b"WELSPECS\n/", "text/plain"))],
    )
    assert res.status_code == 200, res.text
    reply = [c for c in calls if c["action"] == "reply"][0]
    names = {v[0] for v in (reply["files"] or {}).values()}
    assert "root.inc" in names
    assert "WELLS.INC" in names
    assert str(reply["hitl_new_attachments"]).lower() == "true"


def test_merge_binaries_keeps_start_schedule_and_adds_hitl() -> None:
    from app.main import _merge_binaries

    base = {
        "file": ("dates.xlsx", b"PK", "application/xlsx"),
        "schedule_files": ("root.inc", b"DATES\n/", "text/plain"),
    }
    extra = {"schedule_files": ("WELLS.INC", b"WELSPECS\n/", "text/plain")}
    out = _merge_binaries(base, extra)
    names = {v[0] for v in out.values()}
    assert "dates.xlsx" in names
    assert "root.inc" in names
    assert "WELLS.INC" in names


def test_state_persist_survives_reload(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "activity_state.json"
    monkeypatch.setenv("ACTIVITY_STATE_PATH", str(path))
    # reload path resolution
    import app.persist as persist_mod
    import app.main as main_mod

    monkeypatch.setattr(persist_mod, "state_path", lambda: path)
    monkeypatch.setattr(main_mod, "save_state", persist_mod.save_state)
    monkeypatch.setattr(main_mod, "load_state", persist_mod.load_state)

    seed = client.post("/v1/demo/seed", headers=KEY).json()
    task_id = seed["task_id"]
    assert path.is_file()

    # Simulate process restart: clear memory then load from disk.
    main_mod._tasks.clear()
    main_mod._order.clear()
    tasks, order = persist_mod.load_state()
    main_mod._tasks.update(tasks)
    main_mod._order.extend(order)

    feed = client.get(f"/v1/tasks/{task_id}").json()
    assert feed["task_id"] == task_id
    assert feed["objective"]
    assert len(feed["activity"]) >= 1


def test_hydrate_list_and_feed_from_payload() -> None:
    listed = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_hydrate_1",
                    "title": "Hydrate demo",
                    "status": "running",
                    "version": 2,
                    "updated_at": "2026-08-16T10:00:00+00:00",
                }
            ],
        },
    )
    assert listed.status_code == 200
    assert listed.json()["list_applied"] == 1
    catalog = client.get("/v1/tasks").json()
    assert any(t["task_id"] == "eng_hydrate_1" for t in catalog["tasks"])

    fed = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_hydrate_1",
            "title": "Hydrate demo",
            "objective": "Full hydrate objective text for the Activity request panel.",
            "status": "awaiting_human",
            "version": 3,
            "human_gate": {
                "gate_id": "g1",
                "expected_version": 3,
                "question": "Approve?",
                "options": ["approve", "reject"],
            },
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "eng_hydrate_1",
                    "at": "2026-08-16T10:01:00+00:00",
                    "status": "EXCEL_EVIDENCE_READY",
                    "summary": "Excel ready",
                    "brief": "Excel вернул факты.",
                    "from_role": "Excel",
                    "to_role": "Builder",
                }
            ],
        },
    )
    assert fed.status_code == 200
    assert fed.json()["feed"]["stored"] is True
    snap = client.get("/v1/tasks/eng_hydrate_1").json()
    assert snap["title"] == "Hydrate demo"
    assert snap["objective"] == "Full hydrate objective text for the Activity request panel."
    assert snap["awaiting_human"] is True
    assert len(snap["activity"]) >= 1


def test_durable_list_pulls_webhook(monkeypatch) -> None:
    async def fake_list():
        return {
            "contract": "mas_activity_task_list",
            "source": "engineering_orchestrator_tasks_v1",
            "count": 1,
            "tasks": [
                {
                    "task_id": "eng_from_dt",
                    "title": "From Data Table",
                    "status": "completed",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                }
            ],
        }

    monkeypatch.setenv("ACTIVITY_LIST_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_list", fake_list)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    res = client.get("/v1/tasks?durable=1")
    assert res.status_code == 200
    body = res.json()
    assert body["durable_hydrate"] is True
    assert body["hydrate"]["applied"] == 1
    assert body["hydrate"]["pruned"] is True
    assert body["hydrate"]["catalog_complete"] is True
    assert any(t["task_id"] == "eng_from_dt" for t in body["tasks"])


def test_durable_feed_pulls_webhook(monkeypatch) -> None:
    async def fake_feed(task_id: str):
        assert task_id == "eng_feed_dt"
        return {
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_feed_dt",
            "title": "Feed from DT",
            "status": "running",
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "eng_feed_dt",
                    "at": "2026-08-16T12:01:00+00:00",
                    "status": "PLAN_READY",
                    "summary": "Plan ready",
                    "from_role": "Orchestrator",
                    "to_role": "Builder",
                }
            ],
        }

    monkeypatch.setenv("ACTIVITY_FEED_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_feed", fake_feed)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    res = client.get("/v1/tasks/eng_feed_dt?durable=1")
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Feed from DT"
    assert len(body["activity"]) >= 1
    assert body["durable_hydrate"] is True


def test_split_hydrate_combined_and_legacy_contracts() -> None:
    from app.durable import split_hydrate

    list_p, feed_p = split_hydrate(
        {
            "contract": "mas_activity_hydrate",
            "list": {"contract": "mas_activity_task_list", "tasks": [{"task_id": "eng_a"}]},
            "feed": {"contract": "mas_activity_feed_hydrate", "ok": True, "task_id": "eng_a"},
        }
    )
    assert list_p["contract"] == "mas_activity_task_list"
    assert feed_p["ok"] is True
    assert split_hydrate({"contract": "mas_activity_task_list", "tasks": []})[1] is None
    assert split_hydrate({"contract": "mas_activity_feed_hydrate", "ok": True})[0] is None
    legacy_list, legacy_feed = split_hydrate({"tasks": [{"task_id": "eng_b"}]})
    assert legacy_list["tasks"][0]["task_id"] == "eng_b"
    assert legacy_feed is None


def test_durable_combined_hydrate_applies_list_and_feed(monkeypatch) -> None:
    async def fake_feed(task_id: str):
        assert task_id == "eng_combo_1"
        return {
            "contract": "mas_activity_hydrate",
            "list": {
                "contract": "mas_activity_task_list",
                "source": "engineering_orchestrator_tasks_v1",
                "count": 1,
                "tasks": [
                    {
                        "task_id": "eng_combo_1",
                        "title": "Combo list",
                        "status": "running",
                        "updated_at": "2026-08-16T12:00:00+00:00",
                    }
                ],
            },
            "feed": {
                "contract": "mas_activity_feed_hydrate",
                "ok": True,
                "task_id": "eng_combo_1",
                "title": "Combo feed",
                "status": "running",
                "source": {"truncated": False, "trace_rows": 1, "handoff_events": 1},
                "events": [
                    {
                        "event_type": "handoff",
                        "task_id": "eng_combo_1",
                        "at": "2026-08-16T12:01:00+00:00",
                        "status": "PLAN_READY",
                        "summary": "Plan ready",
                        "from_role": "Orchestrator",
                        "to_role": "Builder",
                    }
                ],
            },
        }

    monkeypatch.setenv("ACTIVITY_HYDRATE_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_feed", fake_feed)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    res = client.get("/v1/tasks/eng_combo_1?durable=1")
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "Combo feed"
    assert len(body["activity"]) >= 1
    assert body["hydrate"]["ok"] is True
    assert body["hydrate"]["truncated"] is False
    listed = client.get("/v1/tasks").json()
    assert any(t["task_id"] == "eng_combo_1" for t in listed["tasks"])


def test_post_hydrate_combined_contract() -> None:
    res = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_hydrate",
            "list": {
                "contract": "mas_activity_task_list",
                "tasks": [
                    {
                        "task_id": "eng_post_combo",
                        "title": "Posted list",
                        "status": "running",
                        "updated_at": "2026-08-16T12:00:00+00:00",
                    }
                ],
            },
            "feed": {
                "contract": "mas_activity_feed_hydrate",
                "ok": True,
                "task_id": "eng_post_combo",
                "title": "Posted feed",
                "status": "running",
                "events": [],
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["list_applied"] == 1
    assert body["feed"]["stored"] is not False
    snap = client.get("/v1/tasks/eng_post_combo").json()
    assert snap["title"] == "Posted feed"


def test_list_hydrate_preserves_newest_first_and_hitl_flag() -> None:
    listed = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_new_1",
                    "title": "Newest",
                    "status": "awaiting_human",
                    "updated_at": "2026-08-16T15:00:00+00:00",
                    "awaiting_human": True,
                    "human_gate": {
                        "gate_id": "g_new",
                        "expected_version": 2,
                        "kind": "needs_approval",
                        "reason": "Approve?",
                    },
                },
                {
                    "task_id": "eng_old_1",
                    "title": "Oldest",
                    "status": "completed",
                    "updated_at": "2026-08-16T10:00:00+00:00",
                    "awaiting_human": False,
                },
            ],
        },
    )
    assert listed.status_code == 200
    catalog = client.get("/v1/tasks").json()["tasks"]
    ids = [t["task_id"] for t in catalog if t["task_id"] in {"eng_new_1", "eng_old_1"}]
    assert ids[0] == "eng_new_1"
    newest = next(t for t in catalog if t["task_id"] == "eng_new_1")
    assert newest["awaiting_human"] is True


def test_sync_dedupes_after_feed_hydrate() -> None:
    event = {
        "event_type": "handoff",
        "event_id": "evt_dup_1",
        "task_id": "eng_dedupe_1",
        "trace_id": "tr_1",
        "at": "2026-08-16T12:00:00+00:00",
        "status": "EXCEL_EVIDENCE_READY",
        "summary": "Excel ready",
        "brief": "Excel вернул факты.",
        "handoff": {
            "from_role": "Excel",
            "to_role": "Builder",
            "details": {"event_id": "evt_dup_1"},
        },
    }
    hydrate = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_dedupe_1",
            "title": "Dedupe",
            "status": "running",
            "events": [event],
        },
    )
    assert hydrate.status_code == 200
    before = client.get("/v1/tasks/eng_dedupe_1").json()
    assert len(before["activity"]) == 1

    synced = client.post(
        "/v1/sync",
        headers=KEY,
        json={"task_id": "eng_dedupe_1", "trace_id": "tr_1", "events": [event]},
    )
    assert synced.status_code == 200
    assert synced.json()["count"] == 0
    after = client.get("/v1/tasks/eng_dedupe_1").json()
    assert len(after["activity"]) == 1


def test_request_panel_script_does_not_wipe_label() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "requestPanel.textContent" not in js
    assert "requestText.textContent" in js


def test_list_hydrate_replaces_shorter_cas_objective() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_obj_1",
                    "title": "Long local",
                    "objective": "Very long local objective from demo seed that should be replaceable",
                    "status": "running",
                    "updated_at": "2026-08-16T10:00:00+00:00",
                }
            ],
        },
    )
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_obj_1",
                    "title": "CAS short",
                    "objective": "CAS short",
                    "status": "running",
                    "updated_at": "2026-08-16T11:00:00+00:00",
                }
            ],
        },
    )
    snap = client.get("/v1/tasks/eng_obj_1").json()
    assert snap["objective"] == "CAS short"


def test_durable_list_prunes_ghost_tasks(monkeypatch) -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_ghost_1",
                    "title": "Ghost",
                    "status": "completed",
                    "updated_at": "2026-08-16T09:00:00+00:00",
                }
            ],
        },
    )
    assert any(t["task_id"] == "eng_ghost_1" for t in client.get("/v1/tasks").json()["tasks"])

    async def fake_list():
        return {
            "contract": "mas_activity_task_list",
            "source": "engineering_orchestrator_tasks_v1",
            # count ≤ returned rows → full catalog, safe to prune ghosts
            "count": 1,
            "tasks": [
                {
                    "task_id": "eng_alive_1",
                    "title": "Alive",
                    "status": "running",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                }
            ],
        }

    monkeypatch.setenv("ACTIVITY_LIST_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_list", fake_list)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    body = client.get("/v1/tasks?durable=1").json()
    ids = {t["task_id"] for t in body["tasks"]}
    assert "eng_alive_1" in ids
    assert "eng_ghost_1" not in ids
    assert body["hydrate"]["pruned"] is True
    assert body["hydrate"]["catalog_complete"] is True


def test_durable_list_keeps_tasks_when_page_truncated(monkeypatch) -> None:
    """List WF returns ≤200 newest; older CAS still in DT must not be treated as ghosts."""
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_old_1",
                    "title": "Older than page",
                    "status": "completed",
                    "updated_at": "2026-08-01T09:00:00+00:00",
                    "version": 3,
                }
            ],
        },
    )
    # Seed a turn/gate so eviction would be user-visible loss.
    client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_old_1",
            "status": "awaiting_human",
            "human_gate": {"gate_id": "g_old", "question": "keep me"},
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "eng_old_1",
                    "at": "2026-08-01T09:01:00+00:00",
                    "status": "AWAITING_HUMAN",
                    "summary": "old turn",
                    "from_role": "Orchestrator",
                    "to_role": "Human",
                }
            ],
        },
    )

    async def fake_list():
        return {
            "contract": "mas_activity_task_list",
            "source": "engineering_orchestrator_tasks_v1",
            "count": 250,  # more than returned page → truncated
            "tasks": [
                {
                    "task_id": "eng_new_1",
                    "title": "Newest page",
                    "status": "running",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                }
            ],
        }

    monkeypatch.setenv("ACTIVITY_LIST_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_list", fake_list)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    body = client.get("/v1/tasks?durable=1").json()
    ids = {t["task_id"] for t in body["tasks"]}
    assert "eng_new_1" in ids
    assert "eng_old_1" in ids
    assert body["hydrate"]["pruned"] is False
    assert body["hydrate"]["catalog_complete"] is False
    feed = client.get("/v1/tasks/eng_old_1").json()
    assert len(feed["activity"]) >= 1
    assert feed["human_gate"]["gate_id"] == "g_old"

def test_durable_list_keeps_local_presentation_tasks(monkeypatch) -> None:
    seed = client.post("/v1/demo/seed", headers=KEY).json()
    local_id = seed["task_id"]
    assert local_id.startswith("demo_")

    async def fake_list():
        return {
            "contract": "mas_activity_task_list",
            "source": "engineering_orchestrator_tasks_v1",
            "tasks": [
                {
                    "task_id": "eng_cas_only",
                    "title": "CAS",
                    "status": "running",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                }
            ],
        }

    monkeypatch.setenv("ACTIVITY_LIST_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_list", fake_list)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    body = client.get("/v1/tasks?durable=1").json()
    ids = {t["task_id"] for t in body["tasks"]}
    assert "eng_cas_only" in ids
    assert local_id in ids


def test_list_hydrate_does_not_evict_mid_catalog(monkeypatch) -> None:
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "MAX_TASKS", 2)
    # Newest-first payload larger than MAX_TASKS. Mid-loop eviction would drop eng_a
    # before final reorder, leaving a hole; deferred trim keeps the newest two.
    listed = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {"task_id": "eng_a", "title": "A", "status": "running", "updated_at": "2026-08-16T13:00:00+00:00"},
                {"task_id": "eng_b", "title": "B", "status": "running", "updated_at": "2026-08-16T12:00:00+00:00"},
                {"task_id": "eng_c", "title": "C", "status": "running", "updated_at": "2026-08-16T11:00:00+00:00"},
            ],
        },
    )
    assert listed.status_code == 200
    assert listed.json()["list_applied"] == 3
    catalog = client.get("/v1/tasks").json()["tasks"]
    ids = [t["task_id"] for t in catalog]
    assert ids == ["eng_a", "eng_b"]
    assert "eng_c" not in ids


def test_list_hydrate_clears_stale_gate_on_non_awaiting_status() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_gate_1",
                    "title": "Gate",
                    "status": "awaiting_human",
                    "updated_at": "2026-08-16T10:00:00+00:00",
                    "awaiting_human": True,
                    "human_gate": {
                        "gate_id": "stale-gate",
                        "expected_version": 2,
                        "question": "Old question?",
                    },
                }
            ],
        },
    )
    assert client.get("/v1/tasks/eng_gate_1").json()["awaiting_human"] is True

    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_gate_1",
                    "title": "Gate",
                    "status": "running",
                    "updated_at": "2026-08-16T11:00:00+00:00",
                }
            ],
        },
    )
    snap = client.get("/v1/tasks/eng_gate_1").json()
    assert snap["status"] == "running"
    assert snap["awaiting_human"] is False
    assert snap.get("human_gate") in (None, {})


def test_feed_hydrate_clears_turns_when_events_empty() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_empty_feed",
            "title": "Had turns",
            "status": "running",
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "eng_empty_feed",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "PLAN_READY",
                    "summary": "Old turn",
                    "from_role": "Orchestrator",
                    "to_role": "Builder",
                }
            ],
        },
    )
    assert len(client.get("/v1/tasks/eng_empty_feed").json()["activity"]) == 1

    cleared = client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_empty_feed",
            "title": "Cleared",
            "status": "completed",
            "events": [],
        },
    )
    assert cleared.status_code == 200
    snap = client.get("/v1/tasks/eng_empty_feed").json()
    assert snap["status"] == "completed"
    assert snap["activity"] == []


def test_feed_hydrate_keeps_live_sync_turns() -> None:
    client.post(
        "/v1/sync",
        headers=KEY,
        json={
            "task_id": "eng_live_keep",
            "events": [
                {
                    "event_type": "handoff",
                    "event_id": "live_1",
                    "task_id": "eng_live_keep",
                    "at": "2026-08-16T12:05:00+00:00",
                    "status": "PLAN_READY",
                    "summary": "Live only",
                    "from_role": "Orchestrator",
                    "to_role": "Builder",
                }
            ],
        },
    )
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_live_keep",
            "title": "CAS",
            "status": "running",
            "version": 1,
            "events": [
                {
                    "event_type": "handoff",
                    "event_id": "cas_1",
                    "task_id": "eng_live_keep",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "DELEGATED",
                    "summary": "From CAS",
                    "from_role": "Orchestrator",
                    "to_role": "Excel",
                }
            ],
        },
    )
    snap = client.get("/v1/tasks/eng_live_keep").json()
    summaries = [t.get("summary") or t.get("text") for t in snap["activity"]]
    assert any("From CAS" in str(s) for s in summaries)
    assert any("Live only" in str(s) for s in summaries)


def test_feed_hydrate_does_not_regress_version() -> None:
    client.post(
        "/v1/sync",
        headers=KEY,
        json={"task_id": "eng_feed_ver", "version": 9, "status": "running", "events": []},
    )
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_feed_ver",
            "status": "running",
            "version": 3,
            "events": [],
        },
    )
    assert client.get("/v1/tasks/eng_feed_ver").json()["version"] == 9


def test_feed_hydrate_clears_stale_gate_when_cas_omits() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_gate_clear",
            "status": "awaiting_human",
            "version": 2,
            "human_gate": {"gate_id": "old", "expected_version": 2, "question": "Old?"},
            "events": [],
        },
    )
    assert client.get("/v1/tasks/eng_gate_clear").json()["awaiting_human"] is True
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_gate_clear",
            "status": "awaiting_human",
            "version": 2,
            "human_gate": None,
            "events": [],
        },
    )
    snap = client.get("/v1/tasks/eng_gate_clear").json()
    assert snap["awaiting_human"] is False
    assert snap.get("human_gate") in (None, {})
    assert snap["status"] == "running"


def test_list_hydrate_no_orphan_awaiting_without_gate() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_orphan_hitl",
                    "title": "Orphan",
                    "status": "awaiting_human",
                    "awaiting_human": False,
                    "updated_at": "2026-08-16T10:00:00+00:00",
                }
            ],
        },
    )
    snap = client.get("/v1/tasks/eng_orphan_hitl").json()
    assert snap["status"] == "running"
    assert snap["awaiting_human"] is False
    row = next(t for t in client.get("/v1/tasks").json()["tasks"] if t["task_id"] == "eng_orphan_hitl")
    assert row["turn_count"] is None


def test_runtime_activity_state_not_in_repo() -> None:
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    assert "mas-activity-service/data/" in gitignore
    # Runtime may recreate the file locally; it must stay untracked.
    import subprocess

    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT.parent), "ls-files", "mas-activity-service/data/activity_state.json"],
        text=True,
    ).strip()
    assert tracked == ""


def test_list_hydrate_does_not_regress_version() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_ver_1",
                    "title": "V",
                    "status": "awaiting_human",
                    "version": 5,
                    "updated_at": "2026-08-16T10:00:00+00:00",
                    "human_gate": {"gate_id": "g", "expected_version": 5},
                }
            ],
        },
    )
    # Trace Writer advanced version locally after CAS row snapshot.
    client.post(
        "/v1/sync",
        headers=KEY,
        json={"task_id": "eng_ver_1", "version": 7, "status": "awaiting_human", "events": []},
    )
    assert client.get("/v1/tasks/eng_ver_1").json()["version"] == 7

    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_ver_1",
                    "title": "V",
                    "status": "awaiting_human",
                    "version": 5,
                    "updated_at": "2026-08-16T09:00:00+00:00",
                    "human_gate": {"gate_id": "g", "expected_version": 5},
                }
            ],
        },
    )
    assert client.get("/v1/tasks/eng_ver_1").json()["version"] == 7


def test_trim_prefers_evicting_cas_over_local(monkeypatch) -> None:
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "MAX_TASKS", 2)
    seed = client.post("/v1/demo/seed", headers=KEY).json()
    local_id = seed["task_id"]
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {"task_id": "eng_x", "title": "X", "status": "running", "updated_at": "2026-08-16T13:00:00+00:00"},
                {"task_id": "eng_y", "title": "Y", "status": "running", "updated_at": "2026-08-16T12:00:00+00:00"},
            ],
        },
    )
    ids = {t["task_id"] for t in client.get("/v1/tasks").json()["tasks"]}
    assert local_id in ids
    assert len(ids) == 2
    assert "eng_y" not in ids or "eng_x" in ids


def test_empty_rail_auto_list_hydrate_is_oneshot(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake_list():
        calls["n"] += 1
        return {"contract": "mas_activity_task_list", "tasks": []}

    monkeypatch.setenv("ACTIVITY_LIST_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_list", fake_list)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    first = client.get("/v1/tasks").json()
    second = client.get("/v1/tasks").json()
    assert calls["n"] == 1
    assert first.get("hydrate", {}).get("auto_empty") is True
    assert "hydrate" not in second

    client.get("/v1/tasks?durable=1")
    assert calls["n"] == 2


def test_feed_hydrate_failure_keeps_prior_turns(monkeypatch) -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_keep_turns",
            "title": "Keep",
            "status": "running",
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "eng_keep_turns",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "PLAN_READY",
                    "summary": "Prior",
                    "from_role": "Orchestrator",
                    "to_role": "Builder",
                }
            ],
        },
    )
    assert len(client.get("/v1/tasks/eng_keep_turns").json()["activity"]) == 1

    async def boom(*_a, **_k):
        raise RuntimeError("append failed")

    monkeypatch.setattr("app.main._append_turns", boom)
    with pytest.raises(RuntimeError, match="append failed"):
        client.post(
            "/v1/hydrate",
            headers=KEY,
            json={
                "contract": "mas_activity_feed_hydrate",
                "ok": True,
                "task_id": "eng_keep_turns",
                "title": "Keep",
                "status": "completed",
                "events": [
                    {
                        "event_type": "handoff",
                        "task_id": "eng_keep_turns",
                        "at": "2026-08-16T13:00:00+00:00",
                        "status": "DONE",
                        "summary": "New",
                        "from_role": "Builder",
                        "to_role": "Orchestrator",
                    }
                ],
            },
        )
    snap = client.get("/v1/tasks/eng_keep_turns").json()
    assert len(snap["activity"]) == 1
    prior = snap["activity"][0]
    assert "Prior" in str(prior.get("summary") or prior.get("text") or prior.get("brief") or "")


def test_list_hydrate_keeps_newer_local_updated_at() -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_hot_1",
                    "title": "Hot",
                    "status": "running",
                    "updated_at": "2026-08-16T18:00:00+00:00",
                },
                {
                    "task_id": "eng_quiet_1",
                    "title": "Quiet",
                    "status": "completed",
                    "updated_at": "2026-08-16T17:00:00+00:00",
                },
            ],
        },
    )
    # CAS returns older stamp for the hot task (stale row) while quiet is unchanged.
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_task_list",
            "tasks": [
                {
                    "task_id": "eng_quiet_1",
                    "title": "Quiet",
                    "status": "completed",
                    "updated_at": "2026-08-16T17:00:00+00:00",
                },
                {
                    "task_id": "eng_hot_1",
                    "title": "Hot",
                    "status": "running",
                    "updated_at": "2026-08-16T12:00:00+00:00",
                },
            ],
        },
    )
    catalog = client.get("/v1/tasks").json()["tasks"]
    ids = [t["task_id"] for t in catalog if t["task_id"] in {"eng_hot_1", "eng_quiet_1"}]
    assert ids[0] == "eng_hot_1"
    hot = next(t for t in catalog if t["task_id"] == "eng_hot_1")
    assert hot["updated_at"].startswith("2026-08-16T18:00:00")


def test_durable_feed_surfaces_ok_false(monkeypatch) -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_stale_1",
            "title": "Cached",
            "status": "running",
            "events": [
                {
                    "event_type": "handoff",
                    "task_id": "eng_stale_1",
                    "at": "2026-08-16T12:00:00+00:00",
                    "status": "PLAN_READY",
                    "summary": "Cached turn",
                    "from_role": "Orchestrator",
                    "to_role": "Builder",
                }
            ],
        },
    )

    async def fake_feed(task_id: str):
        return {
            "contract": "mas_activity_feed_hydrate",
            "ok": False,
            "task_id": task_id,
            "error": "task not found in Data Table",
        }

    monkeypatch.setenv("ACTIVITY_FEED_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_feed", fake_feed)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    res = client.get("/v1/tasks/eng_stale_1?durable=1")
    assert res.status_code == 200
    body = res.json()
    assert body["hydrate"]["ok"] is False
    assert "not found" in body["hydrate"]["error"]
    assert len(body["activity"]) >= 1


def test_durable_feed_surfaces_webhook_error(monkeypatch) -> None:
    client.post(
        "/v1/hydrate",
        headers=KEY,
        json={
            "contract": "mas_activity_feed_hydrate",
            "ok": True,
            "task_id": "eng_err_1",
            "title": "Cached",
            "status": "running",
            "events": [],
        },
    )

    async def boom(task_id: str):
        raise RuntimeError("Activity feed hydrate HTTP 500: boom")

    monkeypatch.setenv("ACTIVITY_FEED_URL", "http://n8n.test/webhook/mas-activity-hydrate")
    monkeypatch.setattr("app.main.fetch_task_feed", boom)
    monkeypatch.setattr("app.main.durable_enabled", lambda: True)

    res = client.get("/v1/tasks/eng_err_1?durable=1")
    assert res.status_code == 200
    body = res.json()
    assert body["hydrate"]["ok"] is False
    assert "HTTP 500" in body["hydrate"]["error"]


def test_feed_hydrate_workflow_limit_matches_max_turns() -> None:
    gen = Path(__file__).resolve().parents[2] / "n8n" / "templates" / "generate_activity_hydrate_workflows.py"
    text = gen.read_text(encoding="utf-8")
    assert '"limit": 500' in text or '"limit":500' in text
    assert "truncated" in text
    assert '"orderBy": True' in text or '"orderBy":True' in text
    assert 'orderByDirection": "DESC"' in text or "orderByDirection\": \"DESC\"" in text
    wf = Path(__file__).resolve().parents[2] / "n8n" / "workflows" / "retired" / "mas-activity-hydrate.workflow.json"
    assert wf.is_file()
    wf_text = wf.read_text(encoding="utf-8")
    assert '"limit": 500' in wf_text
    assert '"orderBy": true' in wf_text
    assert '"orderByDirection": "DESC"' in wf_text
