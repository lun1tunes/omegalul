"""Readiness probes must not call httpx with relative webhook URLs."""

from __future__ import annotations

import asyncio

from app.readiness import probe_n8n_stack
from app.settings import Settings


def _clear_n8n_env(monkeypatch) -> None:
    for key in (
        "ORCHESTRATOR_WEBHOOK_URL",
        "N8N_BASE_URL",
        "N8N_USERNAME",
        "N8N_PASSWORD",
        "ACTIVITY_HYDRATE_URL",
        "ACTIVITY_LIST_URL",
        "ACTIVITY_FEED_URL",
        "KNOWLEDGE_INGEST_URL",
        "N8N_WEBHOOK_CHECKS",
        "ACTIVITY_TLS_VERIFY",
        "ACTIVITY_CA_BUNDLE",
    ):
        monkeypatch.delenv(key, raising=False)


class _RecordingClient:
    def __init__(self, *args, **kwargs) -> None:
        self.urls: list[str] = kwargs.pop("urls")
        self.verify = kwargs.get("verify")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, method, url, **kwargs):
        self.urls.append(url)
        raise AssertionError(f"unexpected probe request {method} {url}")


def test_probe_skips_http_when_n8n_base_empty(monkeypatch) -> None:
    _clear_n8n_env(monkeypatch)
    seen: list[str] = []

    def _client(*args, **kwargs):
        return _RecordingClient(*args, urls=seen, **kwargs)

    monkeypatch.setattr("app.readiness.httpx.AsyncClient", _client)
    report = asyncio.run(probe_n8n_stack(Settings(), timeout_s=1.0))
    assert seen == []
    assert report["ready"] is False
    assert "extra_webhooks" not in report["checks"]


def test_probe_skips_relative_orchestrator_webhook(monkeypatch) -> None:
    _clear_n8n_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "/webhook/engineering-orchestrator")
    monkeypatch.setenv("N8N_WEBHOOK_CHECKS", "engineering-orchestrator,mas-deployment-health-check")
    seen: list[str] = []

    def _client(*args, **kwargs):
        return _RecordingClient(*args, urls=seen, **kwargs)

    monkeypatch.setattr("app.readiness.httpx.AsyncClient", _client)
    report = asyncio.run(probe_n8n_stack(Settings(), timeout_s=1.0))
    assert seen == []
    assert report["checks"]["orchestrator"].get("configured") is False
    assert "extra_webhooks" not in report["checks"]


def test_probe_passes_tls_verify_to_httpx(monkeypatch) -> None:
    _clear_n8n_env(monkeypatch)
    monkeypatch.setenv("N8N_BASE_URL", "https://n8n.example")
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "false")
    clients: list[_RecordingClient] = []

    class _ProbeClient(_RecordingClient):
        async def request(self, method, url, **kwargs):
            self.urls.append(url)
            return type(
                "Response",
                (),
                {
                    "status_code": 503,
                    "text": "offline",
                    "json": lambda _self: {},
                },
            )()

    def _client(*args, **kwargs):
        client = _ProbeClient(*args, urls=[], **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("app.readiness.httpx.AsyncClient", _client)
    report = asyncio.run(probe_n8n_stack(Settings(), timeout_s=1.0))
    assert clients and clients[0].verify is False
    assert report["ready"] is False


class _StatusResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _CaptureClient:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "json": kwargs.get("json")})
        return self.handler(method, url, kwargs)


def test_orchestrator_probe_sends_case_id_and_rejects_500(monkeypatch) -> None:
    _clear_n8n_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://n8n.example/webhook/mas-orchestrator-step")
    monkeypatch.setenv("N8N_BASE_URL", "http://n8n.example")
    captured: list[_CaptureClient] = []

    def _handler(method, url, kwargs):
        if url.rstrip("/").endswith("/healthz"):
            return _StatusResponse(200, {"status": "ok"})
        if "mas-orchestrator-step" in url:
            return _StatusResponse(500, text="case_id is required")
        return _StatusResponse(404, text="missing")

    def _client(*args, **kwargs):
        client = _CaptureClient(_handler)
        captured.append(client)
        return client

    monkeypatch.setattr("app.readiness.httpx.AsyncClient", _client)
    report = asyncio.run(probe_n8n_stack(Settings(), timeout_s=1.0))
    orch = [call for call in captured[0].calls if call["method"] == "POST" and "mas-orchestrator-step" in call["url"]]
    assert orch
    body = orch[0]["json"]
    assert body["action"] == "probe"
    assert body["case_id"] == "CASE-readiness-probe"
    assert report["checks"]["orchestrator"]["ok"] is False
    assert report["checks"]["orchestrator"]["http_status"] == 500
    assert report["ready"] is False


def test_orchestrator_probe_200_is_ready_when_healthz_ok(monkeypatch) -> None:
    _clear_n8n_env(monkeypatch)
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://n8n.example/webhook/mas-orchestrator-step")
    monkeypatch.setenv("N8N_BASE_URL", "http://n8n.example")

    def _handler(method, url, kwargs):
        if url.rstrip("/").endswith("/healthz"):
            return _StatusResponse(200, {"status": "ok"})
        if "mas-orchestrator-step" in url and method == "POST":
            return _StatusResponse(200, {"contract": "mas_orchestrator_ack", "case_id": "CASE-readiness-probe"})
        return _StatusResponse(404, text="missing")

    def _client(*args, **kwargs):
        return _CaptureClient(_handler)

    monkeypatch.setattr("app.readiness.httpx.AsyncClient", _client)
    report = asyncio.run(probe_n8n_stack(Settings(), timeout_s=1.0))
    assert report["checks"]["orchestrator"]["ok"] is True
    assert report["checks"]["orchestrator"]["contract"] == "mas_orchestrator_ack"
    assert report["ready"] is True
