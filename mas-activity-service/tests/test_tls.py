"""Outbound n8n clients must consistently honor the TLS policy."""

from __future__ import annotations

import asyncio
import json

from app import durable
from app import orchestrator


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {"ok": True}


class _Client:
    instances: list["_Client"] = []

    def __init__(self, *args, **kwargs):
        self.verify = kwargs.get("verify")
        self.cookies = type("Cookies", (), {"jar": []})()
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        return _Response()


def test_webhook_client_disables_verification_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "https://n8n.example/webhook/orch")
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "false")
    _Client.instances.clear()
    monkeypatch.setattr("app.orchestrator.httpx.AsyncClient", _Client)

    result = asyncio.run(orchestrator._invoke_webhook({"action": "status"}, timeout_s=1.0))

    assert result == {"ok": True}
    assert _Client.instances[0].verify is False


def test_durable_clients_use_ca_bundle(monkeypatch, tmp_path) -> None:
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("dummy", encoding="ascii")
    monkeypatch.setenv("ACTIVITY_LIST_URL", "https://n8n.example/webhook/list")
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "true")
    monkeypatch.setenv("ACTIVITY_CA_BUNDLE", str(ca))
    _Client.instances.clear()
    monkeypatch.setattr("app.durable.httpx.AsyncClient", _Client)

    result = asyncio.run(durable.fetch_task_list(timeout_s=1.0))

    assert result == {"ok": True}
    assert _Client.instances[0].verify == str(ca)


def test_webhook_invalid_json_is_a_502(monkeypatch) -> None:
    class _BadResponse(_Response):
        text = "not-json"

        def json(self):
            raise json.JSONDecodeError("bad", "not-json", 0)

    class _BadClient(_Client):
        async def post(self, *args, **kwargs):
            return _BadResponse()

    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "https://n8n.example/webhook/orch")
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "true")
    monkeypatch.setattr("app.orchestrator.httpx.AsyncClient", _BadClient)

    try:
        asyncio.run(orchestrator._invoke_webhook({"action": "status"}, timeout_s=1.0))
    except orchestrator.OrchestratorError as exc:
        assert exc.status_code == 502
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("invalid webhook JSON must fail closed")
