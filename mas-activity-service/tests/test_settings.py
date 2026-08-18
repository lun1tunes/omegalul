"""Settings load mas-activity.env from the service directory, like excel-tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import Settings, SERVICE_ROOT, _load_env_files


def test_settings_reads_monkeypatched_env(monkeypatch) -> None:
    for key in (
        "N8N_BASE_URL",
        "ACTIVITY_LIST_URL",
        "ACTIVITY_FEED_URL",
        "KNOWLEDGE_INGEST_URL",
        "ACTIVITY_CA_BUNDLE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "http://n8n.example/webhook/engineering-orchestrator")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("MAS_ACTIVITY_PORT", "8201")
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "false")
    settings = Settings()
    assert settings.n8n_transport == "webhook"
    assert settings.n8n_base == "http://n8n.example"
    assert settings.resolved_list_url.endswith("/webhook/mas-activity-list-tasks")
    assert settings.resolved_feed_url.endswith("/webhook/mas-activity-load-feed")
    assert settings.resolved_knowledge_ingest_url.endswith("/webhook/mas-knowledge-ingest")
    assert settings.n8n_health_url == "http://n8n.example/healthz"
    assert settings.log_level == "DEBUG"
    assert settings.port == 8201
    assert settings.tls_verify is False
    assert settings.httpx_verify is False
    assert settings.public_summary()["n8n_transport"] == "webhook"
    assert "ORCHESTRATOR_AUTH_VALUE" not in str(settings.public_summary())
    assert settings.n8n_password == "" or "password" not in settings.public_summary()


def test_unconfigured_without_n8n_urls(monkeypatch) -> None:
    for key in (
        "ORCHESTRATOR_WEBHOOK_URL",
        "N8N_BASE_URL",
        "N8N_USERNAME",
        "N8N_PASSWORD",
        "ACTIVITY_LIST_URL",
        "ACTIVITY_FEED_URL",
        "KNOWLEDGE_INGEST_URL",
        "ACTIVITY_TLS_VERIFY",
        "ACTIVITY_CA_BUNDLE",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.n8n_transport == "unconfigured"
    assert settings.resolved_orchestrator_webhook == ""
    assert settings.n8n_base == ""
    assert settings.n8n_health_url == ""
    assert settings.tls_verify is True
    assert settings.httpx_verify is True
    assert settings.resolved_knowledge_ingest_url == ""


def test_relative_webhook_urls_are_unconfigured(monkeypatch) -> None:
    for key in (
        "N8N_BASE_URL",
        "ACTIVITY_LIST_URL",
        "ACTIVITY_FEED_URL",
        "KNOWLEDGE_INGEST_URL",
        "N8N_USERNAME",
        "N8N_PASSWORD",
        "ACTIVITY_TLS_VERIFY",
        "ACTIVITY_CA_BUNDLE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ORCHESTRATOR_WEBHOOK_URL", "/webhook/engineering-orchestrator")
    settings = Settings()
    assert settings.n8n_base == ""
    assert settings.n8n_transport == "unconfigured"
    assert settings.resolved_orchestrator_webhook == ""
    assert settings.resolved_list_url == ""
    assert settings.resolved_feed_url == ""
    assert settings.resolved_knowledge_ingest_url == ""
    assert settings.n8n_health_url == ""


def test_webhook_url_requires_absolute_http_base() -> None:
    from app.settings import _webhook_url

    assert _webhook_url("", "engineering-orchestrator") == ""
    assert _webhook_url("/n8n", "engineering-orchestrator") == ""
    assert _webhook_url("n8n.example", "engineering-orchestrator") == ""
    assert (
        _webhook_url("http://n8n.example", "engineering-orchestrator")
        == "http://n8n.example/webhook/engineering-orchestrator"
    )


def test_ca_bundle_is_used_without_disabling_tls(monkeypatch, tmp_path) -> None:
    ca = tmp_path / "corp-ca.pem"
    ca.write_text("dummy", encoding="ascii")
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "true")
    monkeypatch.setenv("ACTIVITY_CA_BUNDLE", str(ca))
    settings = Settings()
    assert settings.tls_verify is True
    assert settings.httpx_verify == str(ca)
    assert settings.public_summary()["tls_ca_bundle_configured"] is True


def test_missing_ca_bundle_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ACTIVITY_TLS_VERIFY", "true")
    monkeypatch.setenv("ACTIVITY_CA_BUNDLE", str(tmp_path / "missing.pem"))
    with pytest.raises(ValueError, match="ACTIVITY_CA_BUNDLE"):
        Settings()


def test_env_candidates_include_service_file() -> None:
    assert SERVICE_ROOT.name == "mas-activity-service"
    example = SERVICE_ROOT / "mas-activity.env.example"
    assert example.is_file()
    text = example.read_text(encoding="utf-8")
    assert "MAS_ACTIVITY_KEY" not in text
    assert "HITL_MODE" not in text
    assert "ORCHESTRATOR_WEBHOOK_URL" in text
    assert "KNOWLEDGE_INGEST_URL" in text
    loaded = _load_env_files()
    assert all(isinstance(path, Path) for path in loaded)
