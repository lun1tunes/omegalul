"""Liveness vs n8n readiness probes. Never log secrets."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.settings import STATIC, Settings, _is_absolute_http_url, _webhook_url, get_settings

logger = logging.getLogger(__name__)


def _safe_url(url: str) -> str:
    return url.split("?", 1)[0]


def _ok_http(status: int) -> bool:
    if 200 <= status < 300:
        return True
    # Webhook exists but inbound auth is on, or probe task is missing.
    return status in {400, 401, 403, 409, 422}


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _is_absolute_http_url(url):
        return {
            "ok": False,
            "reachable": False,
            "configured": False,
            "error": "URL is not an absolute http(s) address",
            "elapsed_ms": 0,
        }
    started = time.perf_counter()
    try:
        response = await client.request(method, url, headers=headers or {}, json=json_body)
    except httpx.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.warning("n8n probe %s %s failed in %.0fms: %s", method, _safe_url(url), elapsed_ms, exc)
        return {
            "ok": False,
            "reachable": False,
            "error": str(exc)[:300],
            "elapsed_ms": round(elapsed_ms),
        }
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "n8n probe %s %s -> %s in %.0fms",
        method,
        _safe_url(url),
        response.status_code,
        elapsed_ms,
    )
    body_contract = None
    preview = None
    try:
        parsed = response.json()
        if isinstance(parsed, list) and parsed:
            parsed = parsed[0]
        if isinstance(parsed, dict):
            body_contract = parsed.get("contract")
    except ValueError:
        preview = response.text[:200] or None
    return {
        "ok": _ok_http(response.status_code),
        "reachable": True,
        "http_status": response.status_code,
        "contract": body_contract,
        "response_preview": preview,
        "elapsed_ms": round(elapsed_ms),
    }


def ui_assets_ok() -> bool:
    return STATIC.is_dir() and (STATIC / "index.html").is_file()


async def probe_n8n_stack(
    settings: Settings | None = None, *, timeout_s: float = 8.0
) -> dict[str, Any]:
    """Ping n8n healthz plus the webhooks Activity needs before a real task."""
    settings = settings or get_settings()
    missing: list[str] = []
    checks: dict[str, Any] = {}

    if not ui_assets_ok():
        checks["ui"] = {"ok": False, "error": "static/index.html missing"}
    else:
        checks["ui"] = {"ok": True}

    transport = settings.n8n_transport
    if transport == "unconfigured":
        missing.append("ORCHESTRATOR_WEBHOOK_URL or N8N_BASE_URL")

    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        verify=settings.httpx_verify,
    ) as client:
        if settings.n8n_health_url:
            checks["n8n_healthz"] = await _request(client, "GET", settings.n8n_health_url)
        elif settings.n8n_base:
            checks["n8n_healthz"] = {"ok": False, "error": "N8N_HEALTH_PATH empty"}
        else:
            checks["n8n_healthz"] = {
                "ok": False,
                "configured": False,
                "note": "Set N8N_BASE_URL to probe /healthz",
            }

        orch_url = settings.resolved_orchestrator_webhook
        if transport == "webhook" and orch_url:
            checks["orchestrator"] = await _request(
                client,
                "POST",
                orch_url,
                headers=settings.orchestrator_headers() or {"Content-Type": "application/json"},
                json_body={
                    "action": "status",
                    "task_id": "activity_connectivity_probe",
                    "requested_by": "activity-diagnostics",
                },
            )
            checks["orchestrator"]["transport"] = "webhook"
        elif transport == "n8n_rest" and settings.n8n_base:
            login = await _request(
                client,
                "POST",
                f"{settings.n8n_base}/rest/login",
                json_body={
                    "emailOrLdapLoginId": settings.n8n_username,
                    "password": settings.n8n_password,
                },
            )
            checks["orchestrator"] = {
                **login,
                "transport": "n8n_rest",
                "note": "n8n REST login (password not logged)",
            }
        else:
            checks["orchestrator"] = {
                "ok": False,
                "configured": False,
                "error": "Orchestrator webhook/REST is not configured",
            }

        if settings.resolved_hydrate_url:
            hydrate = await _request(
                client,
                "POST",
                settings.resolved_hydrate_url,
                headers=settings.durable_headers(),
                json_body={"action": "list"},
            )
            checks["activity_list"] = hydrate
            checks["activity_feed"] = {
                **hydrate,
                "note": "same webhook as activity_list (mas-activity-hydrate)",
            }
        else:
            missing.append("ACTIVITY_HYDRATE_URL or N8N_BASE_URL")
            checks["activity_list"] = {"ok": False, "configured": False}
            checks["activity_feed"] = {"ok": False, "configured": False}

        extras: list[dict[str, Any]] = []
        seen = {
            settings.resolved_orchestrator_webhook.rstrip("/"),
            settings.resolved_hydrate_url.rstrip("/"),
        }
        for path in settings.webhook_check_paths:
            url = _webhook_url(settings.n8n_base, path)
            if not url:
                continue
            key = url.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            extras.append({"path": path, **(await _request(client, "GET", url))})
        if extras:
            checks["extra_webhooks"] = extras

    required = ("ui", "n8n_healthz", "orchestrator", "activity_list", "activity_feed")
    ready = not missing and all(checks.get(name, {}).get("ok") for name in required)
    extras_failed = [
        item["path"]
        for item in checks.get("extra_webhooks") or []
        if isinstance(item, dict) and not item.get("ok")
    ]
    status = "ready" if ready and not extras_failed else ("degraded" if ready else "not_ready")
    if extras_failed and ready:
        # Extra form webhooks are informational; core path is enough to start a task.
        status = "ready"
        ready = True

    return {
        "ready": ready,
        "status": status if ready else "not_ready",
        "n8n_transport": transport,
        "missing_config": missing,
        "checks": checks,
    }
