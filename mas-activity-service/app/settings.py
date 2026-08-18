"""Service settings. Env files are loaded from the service directory, like excel-tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
STATIC = SERVICE_ROOT / "static"
VERSION = "0.6.0"
DEFAULT_ORCHESTRATOR_WORKFLOW_ID = "ba8ba59f-e4e4-5ff6-b22c-63ceae883271"
ORCHESTRATOR_WEBHOOK_PATH = "engineering-orchestrator"
LIST_WEBHOOK_PATH = "mas-activity-list-tasks"
FEED_WEBHOOK_PATH = "mas-activity-load-feed"
DEFAULT_WEBHOOK_CHECKS = (
    ORCHESTRATOR_WEBHOOK_PATH,
    LIST_WEBHOOK_PATH,
    FEED_WEBHOOK_PATH,
    "mas-deployment-health-check",
)

UNCONFIGURED_N8N = (
    "n8n не настроен. В mas-activity.env задайте ORCHESTRATOR_WEBHOOK_URL "
    "или N8N_BASE_URL (для REST ещё N8N_USERNAME и N8N_PASSWORD)."
)


def _load_env_files() -> list[Path]:
    loaded: list[Path] = []
    for path in (
        SERVICE_ROOT / "mas-activity.env",
        SERVICE_ROOT / ".env",
        REPO_ROOT / ".env",
    ):
        if not path.is_file():
            continue
        # utf-8-sig swallows a Windows Notepad BOM that otherwise poisons keys.
        load_dotenv(path, override=False, encoding="utf-8-sig")
        loaded.append(path)
    return loaded


LOADED_ENV_FILES = _load_env_files()


def _is_absolute_http_url(url: str) -> bool:
    value = (url or "").strip()
    return value.startswith("http://") or value.startswith("https://")


def _webhook_url(base: str, path: str) -> str:
    root = (base or "").strip().rstrip("/")
    slug = path.strip().lstrip("/")
    if not _is_absolute_http_url(root) or not slug:
        return ""
    return f"{root}/webhook/{slug}"


class Settings(BaseSettings):
    """All process configuration. Import ``get_settings`` — do not scatter ``os.getenv``."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    host: str = Field(default="127.0.0.1", validation_alias="MAS_ACTIVITY_HOST")
    port: int = Field(default=8200, validation_alias="MAS_ACTIVITY_PORT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    # Keep certificate verification enabled by default. Set
    # ACTIVITY_TLS_VERIFY=false only for a trusted local/self-signed endpoint.
    tls_verify: bool = Field(default=True, validation_alias="ACTIVITY_TLS_VERIFY")
    # Prefer installing the corporate CA and pointing to it over disabling TLS
    # verification. This is passed to httpx as its CA bundle path.
    ca_bundle: str = Field(default="", validation_alias="ACTIVITY_CA_BUNDLE")

    n8n_base_url: str = Field(default="", validation_alias="N8N_BASE_URL")
    n8n_username: str = Field(default="", validation_alias="N8N_USERNAME")
    n8n_password: str = Field(default="", validation_alias="N8N_PASSWORD")
    orchestrator_workflow_id: str = Field(
        default=DEFAULT_ORCHESTRATOR_WORKFLOW_ID,
        validation_alias="ORCHESTRATOR_WORKFLOW_ID",
    )
    orchestrator_webhook_url: str = Field(default="", validation_alias="ORCHESTRATOR_WEBHOOK_URL")
    orchestrator_auth_header: str = Field(default="", validation_alias="ORCHESTRATOR_AUTH_HEADER")
    orchestrator_auth_value: str = Field(default="", validation_alias="ORCHESTRATOR_AUTH_VALUE")

    activity_list_url: str = Field(default="", validation_alias="ACTIVITY_LIST_URL")
    activity_feed_url: str = Field(default="", validation_alias="ACTIVITY_FEED_URL")
    activity_durable_auth_header: str = Field(
        default="", validation_alias="ACTIVITY_DURABLE_AUTH_HEADER"
    )
    activity_durable_auth_value: str = Field(
        default="", validation_alias="ACTIVITY_DURABLE_AUTH_VALUE"
    )

    activity_state_path: str = Field(default="", validation_alias="ACTIVITY_STATE_PATH")
    activity_binaries_path: str = Field(default="", validation_alias="ACTIVITY_BINARIES_PATH")
    mas_knowledge_corpus: str = Field(default="", validation_alias="MAS_KNOWLEDGE_CORPUS")
    n8n_health_path: str = Field(default="/healthz", validation_alias="N8N_HEALTH_PATH")
    n8n_webhook_checks: str = Field(default="", validation_alias="N8N_WEBHOOK_CHECKS")

    @field_validator(
        "n8n_base_url",
        "orchestrator_webhook_url",
        "activity_list_url",
        "activity_feed_url",
        "host",
        "n8n_username",
        "orchestrator_workflow_id",
        "n8n_health_path",
        "ca_bundle",
        mode="before",
    )
    @classmethod
    def _strip(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _log_level(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def _validate_tls_bundle(self) -> "Settings":
        if self.tls_verify and self.ca_bundle:
            path = Path(self.ca_bundle).expanduser()
            if not path.is_file():
                raise ValueError(f"ACTIVITY_CA_BUNDLE does not point to a file: {path}")
        return self

    @property
    def n8n_base(self) -> str:
        if self.n8n_base_url:
            root = self.n8n_base_url.rstrip("/")
        else:
            webhook = self.orchestrator_webhook_url.rstrip("/")
            marker = "/webhook/"
            root = webhook.split(marker, 1)[0] if marker in webhook else ""
        return root if _is_absolute_http_url(root) else ""

    @property
    def httpx_verify(self) -> bool | str:
        """Return the httpx ``verify`` value for outbound n8n requests.

        ``False`` is an explicit local-development escape hatch. In the normal
        verified mode a configured CA bundle is used, which keeps certificate
        verification enabled for corporate/self-signed PKI.
        """
        if not self.tls_verify:
            return False
        bundle = self.ca_bundle.strip()
        if not bundle:
            return True
        path = Path(bundle).expanduser()
        return str(path)

    @property
    def resolved_orchestrator_webhook(self) -> str:
        if self.orchestrator_webhook_url:
            url = self.orchestrator_webhook_url.rstrip("/")
            return url if _is_absolute_http_url(url) else ""
        return _webhook_url(self.n8n_base, ORCHESTRATOR_WEBHOOK_PATH)

    @property
    def resolved_list_url(self) -> str:
        if self.activity_list_url:
            url = self.activity_list_url.strip()
            return url if _is_absolute_http_url(url) else ""
        return _webhook_url(self.n8n_base, LIST_WEBHOOK_PATH)

    @property
    def resolved_feed_url(self) -> str:
        if self.activity_feed_url:
            url = self.activity_feed_url.strip()
            return url if _is_absolute_http_url(url) else ""
        return _webhook_url(self.n8n_base, FEED_WEBHOOK_PATH)

    @property
    def n8n_health_url(self) -> str:
        if not self.n8n_base:
            return ""
        path = self.n8n_health_path if self.n8n_health_path.startswith("/") else f"/{self.n8n_health_path}"
        url = f"{self.n8n_base}{path}"
        return url if _is_absolute_http_url(url) else ""

    @property
    def n8n_transport(self) -> str:
        """Production path only: webhook, n8n_rest, or unconfigured."""
        if _is_absolute_http_url(self.orchestrator_webhook_url):
            return "webhook"
        if self.n8n_base and self.n8n_username and self.n8n_password:
            return "n8n_rest"
        if self.n8n_base:
            return "webhook"
        return "unconfigured"

    @property
    def webhook_check_paths(self) -> list[str]:
        raw = self.n8n_webhook_checks.strip()
        if raw:
            return [part.strip() for part in raw.split(",") if part.strip()]
        return list(DEFAULT_WEBHOOK_CHECKS)

    def orchestrator_headers(self) -> dict[str, str]:
        if self.orchestrator_auth_header and self.orchestrator_auth_value:
            return {self.orchestrator_auth_header: self.orchestrator_auth_value}
        return {}

    def durable_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.activity_durable_auth_header and self.activity_durable_auth_value:
            headers[self.activity_durable_auth_header] = self.activity_durable_auth_value
        return headers

    def public_summary(self) -> dict[str, Any]:
        return {
            "n8n_transport": self.n8n_transport,
            "n8n_base_url": self.n8n_base or None,
            "orchestrator_webhook_configured": bool(self.resolved_orchestrator_webhook),
            "n8n_rest_configured": bool(self.n8n_base and self.n8n_username and self.n8n_password),
            "activity_list_configured": bool(self.resolved_list_url),
            "activity_feed_configured": bool(self.resolved_feed_url),
            "state_persist": bool(self.activity_state_path.strip()),
            "env_files": [str(path) for path in LOADED_ENV_FILES],
            "log_level": self.log_level,
            "tls_verify": self.tls_verify,
            "tls_ca_bundle_configured": bool(self.ca_bundle.strip()),
        }


def get_settings() -> Settings:
    return Settings()


def configure_logging(level: str | None = None) -> None:
    resolved = (level or get_settings().log_level or "INFO").upper()
    logging.basicConfig(
        level=resolved,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
