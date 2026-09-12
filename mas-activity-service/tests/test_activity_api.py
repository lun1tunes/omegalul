"""Unit + API + static asset tests for MAS Activity presentation service."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.pop("CONTROL_PLANE_PROXY_URL", None)
os.environ["CONTROL_PLANE_REQUIRED"] = "false"

from fastapi.testclient import TestClient
import pytest

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
        "CONTROL_PLANE_PROXY_URL",
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
    monkeypatch.setenv("CONTROL_PLANE_REQUIRED", "false")
    reset_store()


def test_ready_health_and_static_assets() -> None:
    health = client.get("/health").json()
    assert health["version"] == VERSION
    assert health["mas_version"] == VERSION
    assert health["n8n_transport"] == "unconfigured"
    assert "state_persist" in health
    assert "auth_required" not in health
    assert "durable_hydrate" not in health
    assert "tasks" not in health
    ready = client.get("/ready")
    assert ready.status_code == 503
    detail = ready.json()["detail"]
    assert detail["ready"] is False
    assert "ORCHESTRATOR_WEBHOOK_URL" in " ".join(detail.get("missing_config") or [])
    index = client.get("/")
    assert index.status_code == 200
    html = index.text
    for anchor in (
        "taskRail", "railList", "railSearch", "newTaskBtn", "brandHome",
        "composer", "startComposer", "taskName", "taskDescription", "startDropzone", "scheduleRoot",
        "requestPanel", "statusBanner", "thread", "notFound", "Задача не найдена", "Вернуться на главную",
        'id="replyBtn"', "hitlDropzone", "hitlAttachBtn", "restartBtn",
        "viewChatBtn", "viewSchemaBtn", ">Чат<", ">Схема<", "schemaView", "schemaTimeline", "schemaPlay",
        'class="gate-panel"', "gatePreview", "gateQuestions",
        "inspector", "resultsPanel", "resultsGroups", "inputsPanel", "diffExpander", "Изменения между версиями",
        "renameTaskBtn", "statusDot", "statusPill", "titleText",
        "NOVATEK RE MASter", "Задачи", "мультиагентная система гидродинамики НОВАТЭК НТЦ",
        'href="/knowledge"',
        'id="resumeRunBtn"', 'id="closeTaskBtn"', 'id="closeTaskHitlBtn"',
    ):
        assert anchor in html, anchor
    for gone in (
        'id="cancelBtn"', 'id="approveBtn"', 'id="rejectBtn"', "taskSelect", "openBtn", "reloadDurableBtn",
        "scheduleDownloadHead", "Data Tables", "Утвердить", "Отклонить", "backendLabel", "liveLabel",
    ):
        assert gone not in html, gone
    assert "app.js?v=108" in html
    assert "schema.js?v=46" in html
    assert "log.js?v=1" in html
    assert "app.css?v=114" in html
    for anchor in ('id="devModeToggle"', "Режим разработчика", 'id="viewLogBtn"', 'id="logView"', 'id="logSteps"', 'id="logDownload"'):
        assert anchor in html, anchor
    log_js = (STATIC / "log.js").read_text(encoding="utf-8")
    assert "/log?format=ndjson" in log_js and "window.MasLog" in log_js

    js_text = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "X-Activity-Key" not in js_text
    assert "mas_activity_key" not in js_text
    assert "alert(" not in js_text
    assert "/v1/tasks" not in js_text
    for needle in (
        'fetch("/cases")', "/cases/${encodeURIComponent(taskId)}/stream", "/answer", "/run", 'method: "PATCH"',
        'fetch("/agents")', "/artifacts/", 'cardsByKind("deliverable")', 'cardsByKind("input")',
        "download_path", "producer",
    ):
        assert needle in js_text, needle
    for needle in (
        "function renderTurn", "function turnRenderKey", "eid:${eid}", "day-sep", "is-cont",
        "deliverableChips", "renderInspector", "result-group", "humanizeGateReason", "humanizeQuestion",
        "looksMachineAsk", "submitHitl", "submitStart", "schedules.unshift(chosen)", "beginRenameTask",
        "feedMatchesOpenTask", "bumpFeedGeneration", 'msg.type === "meta"', "setInterval(() => pollFeed(), 5000)",
        "MasSchema.setAgents", "MasSchema.relayout", "showLoadError(taskId, `Не удалось загрузить задачу (${snap.status}).`)",
        'showLoadError(taskId, "Сеть недоступна при загрузке задачи.")', "li._masTurn = turn", "at_abs", "lane_dir",
        "human_gate ?? data.gate", "duration_label",
        "Ответ принят, ждём оркестратор", "xlsm", "applyResumeWaitHint",
        "hintIfTruncated", "исходный файл расписания",
    ):
        assert needle in js_text, needle
    assert '"task_id" : "task_name"' not in js_text and "`${prefix}: ${ident}" not in js_text
    assert "schedule_artifact" not in js_text
    assert 'q.required ? "обязательно"' not in js_text
    assert "формат: ${q.expected_format}" not in js_text
    for agent in ("excel_extractor", "schedule_builder", "calculation_agent"):
        assert agent not in js_text, agent

    schema_js = (STATIC / "schema.js").read_text(encoding="utf-8")
    for needle in (
        "function buildSchemaFrames", "function applyEvent", "handoff_message", "setAgents", "agentKey",
        "pairVisual", "schema-slip", "schema-peek", "schemaArrowActive", "schemaArrowDone", "schemaArrowError",
        "auto-start-reverse", "deliverableCards", "download_path", "startPlay", "Постановка задачи",
        "Задача завершена. Загрузите результаты работы.", "Ожидает задачу", "prioritizeInputCards",
        "function markOverflow", "function uncollide", 'markerWidth: "14"', "}, 4000);",
        "engineer>orchestrator", "function formatHitl", "Инженер",
        "--orch-w", "is-roomy", "agentFit", "w < 700 || h < 360",
        "function syncNodeChrome", "el.dataset.hint", "node.dataset.hint", "is-hint",
        "const lane = isCompact() ? 36 : 56", "function placePeek",
    ):
        assert needle in schema_js, needle
    for agent in ("excel_extractor", "schedule_builder", "calculation_agent"):
        assert agent not in schema_js, agent

    css = (STATIC / "app.css").read_text(encoding="utf-8")
    for needle in (
        "--brand-blue: #0033A0", "--brand-cyan: #00B8F0", "--brand-ink: #001A57", "--brand-red: #F90D4B",
        ".flash", ".dropzone", ".rail-item", ".turn", ".day-sep", ".file-chip", ".gate-panel", ".composer-box",
        ".inspector", ".result-group", ".result-file", ".schema-node", ".schema-edge", ".schema-slip", ".schema-peek",
        "@media (max-width: 1180px)", "@media (max-width: 860px)", "prefers-reduced-motion",
        "-webkit-line-clamp: 4",
        ".schema-view.is-compact .schema-node-kicker",
        ".schema-view.is-roomy",
        "--orch-w:",
        "--cap-lines:",
        ".schema-node-caption.is-hint",
        "@keyframes peek-in",
    ):
        assert needle in css, needle
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "duration_label" in js.text
    assert "baseline SCHEDULE" not in html
    assert "Workspace" not in html
    assert "baseline" not in js_text
    assert "baseline" not in schema_js

    knowledge = client.get("/knowledge")
    assert knowledge.status_code == 200
    for anchor in ("agentTabs", "agentSelect", "kbSearch", "addBtn", "ingestBtn", "cardList", "createPanel", 'href="/"'):
        assert anchor in knowledge.text, anchor
    assert "knowledge.css?v=101" in knowledge.text
    assert "knowledge.js?v=103" in knowledge.text

    registry = client.get("/registry")
    assert registry.status_code == 200
    for anchor in ("agentList", "addAgentBtn", "createPanel", "createId", "createKind", "createTarget", 'href="/knowledge"', 'href="/"'):
        assert anchor in registry.text, anchor
    assert 'href="/registry"' in html and 'href="/registry"' in knowledge.text
    agents_js = client.get("/static/agents.js")
    assert agents_js.status_code == 200
    assert 'fetch("/agents")' in agents_js.text and "/agents/${encodeURIComponent(agentId)}" in agents_js.text
    for hardcoded in ("excel_extractor", "schedule_builder", "calculation_agent"):
        assert hardcoded not in agents_js.text and hardcoded not in registry.text, hardcoded


def test_cors_preflight_and_get_allow_any_origin() -> None:
    origin = "http://localhost:4173"
    preflight = client.options(
        "/cases",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
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


def test_legacy_task_routes_are_gone() -> None:
    """F7: in-memory /v1/tasks* is deleted; UI uses /cases only."""
    assert client.get("/v1/tasks").status_code == 404
    assert client.get("/v1/tasks/missing_task_zzz").status_code == 404
    assert client.post("/v1/turns", json={"task_id": "x", "turn": {"summary": "x"}}).status_code == 404
    assert client.post("/v1/sync", json={"task_id": "x"}).status_code == 404
    assert client.post("/v1/hydrate", json={"contract": "mas_activity_task_list", "tasks": []}).status_code == 404
    assert client.post("/v1/demo/seed").status_code == 404
    assert client.get("/t/not valid").status_code == 400


def test_rejects_oversized_declared_knowledge_body() -> None:
    huge = client.post(
        "/v1/knowledge/documents",
        headers={"content-length": str(300_000)},
        json={
            "target_base": "schedule_mvp",
            "knowledge_id": "k1",
            "knowledge_type": "note",
            "title": "t",
            "text": "x",
        },
    )
    assert huge.status_code == 413


def test_activity_never_shells_out_to_postgres() -> None:
    """Windows field services reach n8n webhooks only — never compose/psql."""
    text = (ROOT / "app" / "orchestrator.py").read_text(encoding="utf-8")
    assert "_load_execution_data_via_postgres" not in text
    assert "execution_data" not in text
    assert "docker" not in text
    assert "psql" not in text
    plane = (ROOT / "app" / "control_plane.py").read_text(encoding="utf-8")
    assert "psycopg" not in plane
    assert "subprocess" not in plane
    forbidden = ("psycopg", "asyncpg", "sqlalchemy", "psql ", "postgres://", "postgresql://")
    app_root = ROOT / "app"
    for path in app_root.rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in body, (path.name, token)
        assert '["node"' not in body and "['node'" not in body, path.name
    for sibling in (
        ROOT.parent / "excel-agent-tools" / "app",
        ROOT.parent / "schedule-builder-service" / "app",
        ROOT.parent / "fastapi-math-service" / "app",
    ):
        if not sibling.is_dir():
            continue
        for path in sibling.rglob("*.py"):
            body = path.read_text(encoding="utf-8")
            for token in forbidden:
                assert token not in body, (str(path), token)
            assert '["node"' not in body and "['node'" not in body, path
            assert "js_timeline" not in body, path
    for req in (
        ROOT / "requirements.txt",
        ROOT.parent / "excel-agent-tools" / "requirements.txt",
        ROOT.parent / "schedule-builder-service" / "requirements.txt",
        ROOT.parent / "fastapi-math-service" / "requirements.txt",
    ):
        if not req.is_file():
            continue
        req_text = req.read_text(encoding="utf-8").lower()
        assert "psycopg" not in req_text
        assert "asyncpg" not in req_text


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
    assert "Укажите, какой из приложенных schedule-файлов главный." in js
    assert "schedules.length >= 2 && !root" in js
    assert "полным пакетом schedule или без лишних INCLUDE" not in js
    assert "причина обычно есть в ленте" in js
    assert "startSubmitBtn.disabled = true" in js
    assert 'startSubmitBtn.setAttribute("aria-busy", "true")' in js
    assert "formatStartError" in js
    assert "emptyFeedMessage" in js
    assert "hitlDropzone" in js
    assert "Выберите вариант, напишите ответ или приложите файл." in js
    assert "gate-option" in js
    assert 'form.append("choice", choice.value)' in js


def test_request_panel_script_does_not_wipe_label() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "requestPanel.textContent" not in js
    assert "requestText.textContent" in js


def test_runtime_activity_state_not_in_repo() -> None:
    gitignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    assert "mas-activity-service/data/" in gitignore
    import subprocess

    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT.parent), "ls-files", "mas-activity-service/data/activity_state.json"],
        text=True,
    ).strip()
    assert tracked == ""
