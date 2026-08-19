"""Pull durable task catalog / feed from n8n Data Table hydrate webhooks."""

from __future__ import annotations

from typing import Any

import httpx

from app.settings import get_settings


def durable_cfg() -> dict[str, Any]:
    settings = get_settings()
    return {
        "list_url": settings.resolved_list_url,
        "feed_url": settings.resolved_feed_url,
        "auth_header": settings.activity_durable_auth_header,
        "auth_value": settings.activity_durable_auth_value,
        "tls_verify": settings.httpx_verify,
    }


def durable_enabled() -> bool:
    cfg = durable_cfg()
    return bool(cfg["list_url"] or cfg["feed_url"])


def split_hydrate(data: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Split a combined hydrate payload into list and/or feed contracts."""
    if not isinstance(data, dict):
        return None, None
    contract = str(data.get("contract") or "")
    if contract == "mas_activity_hydrate":
        list_p = data.get("list") if isinstance(data.get("list"), dict) else None
        feed_p = data.get("feed") if isinstance(data.get("feed"), dict) else None
        return list_p, feed_p
    if contract == "mas_activity_task_list":
        return data, None
    if contract == "mas_activity_feed_hydrate":
        return None, data
    if isinstance(data.get("tasks"), list) and data.get("events") is None:
        return data, None
    return None, data


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["auth_header"] and cfg["auth_value"]:
        headers[cfg["auth_header"]] = cfg["auth_value"]
    return headers


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    if isinstance(value, list) and value:
        value = value[0]
    return value if isinstance(value, dict) else None


async def probe_durable_connectivity(
    *, task_id: str | None = None, timeout_s: float = 10.0
) -> dict[str, Any]:
    """Probe the n8n webhook bridges used to read Data Tables.

    Activity does not connect to n8n's database directly.  These webhooks are
    the supported Data Table boundary, so the diagnostic intentionally probes
    them instead of making assumptions about the n8n database.
    """
    cfg = durable_cfg()
    result: dict[str, Any] = {
        "configured": bool(cfg["list_url"] or cfg["feed_url"]),
        "list": {"configured": bool(cfg["list_url"])},
        "feed": {"configured": bool(cfg["feed_url"]), "checked": False},
    }

    async with httpx.AsyncClient(timeout=timeout_s, verify=cfg["tls_verify"]) as client:
        if cfg["list_url"]:
            try:
                response = await client.post(
                    cfg["list_url"], json={"action": "list"}, headers=_headers(cfg)
                )
                body = _json_object(response)
                result["list"].update(
                    {
                        "reachable": True,
                        "ok": 200 <= response.status_code < 300 and body is not None,
                        "http_status": response.status_code,
                        "contract": body.get("contract") if body else None,
                        "task_count": len(body.get("tasks", []))
                        if isinstance(body, dict) and isinstance(body.get("tasks"), list)
                        else None,
                    }
                )
                if body is None:
                    result["list"]["error"] = "Response was not a JSON object"
            except httpx.HTTPError as exc:
                result["list"].update(
                    {"reachable": False, "ok": False, "error": str(exc)[:300]}
                )

        if cfg["feed_url"] and task_id:
            result["feed"]["checked"] = True
            try:
                response = await client.post(
                    cfg["feed_url"], json={"task_id": task_id}, headers=_headers(cfg)
                )
                body = _json_object(response)
                result["feed"].update(
                    {
                        "reachable": True,
                        "ok": 200 <= response.status_code < 300 and body is not None,
                        "http_status": response.status_code,
                        "contract": body.get("contract") if body else None,
                        "task_id": task_id,
                    }
                )
                if body is None:
                    result["feed"]["error"] = "Response was not a JSON object"
            except httpx.HTTPError as exc:
                result["feed"].update(
                    {
                        "reachable": False,
                        "ok": False,
                        "task_id": task_id,
                        "error": str(exc)[:300],
                    }
                )
        elif cfg["feed_url"]:
            result["feed"]["note"] = "Provide ?task_id=... to probe the trace/feed Data Table."

    result["ok"] = bool(
        result["list"].get("ok")
        and (not result["feed"].get("checked") or result["feed"].get("ok"))
    )
    return result


async def fetch_task_list(*, timeout_s: float = 30.0) -> dict[str, Any] | None:
    cfg = durable_cfg()
    if not cfg["list_url"]:
        return None
    async with httpx.AsyncClient(timeout=timeout_s, verify=cfg["tls_verify"]) as client:
        res = await client.post(cfg["list_url"], json={"action": "list"}, headers=_headers(cfg))
    if res.status_code >= 400:
        raise RuntimeError(f"Activity list hydrate HTTP {res.status_code}: {res.text[:400]}")
    data = res.json()
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("Activity list hydrate returned non-object")
    return data


async def fetch_task_feed(task_id: str, *, timeout_s: float = 45.0) -> dict[str, Any] | None:
    cfg = durable_cfg()
    if not cfg["feed_url"]:
        return None
    async with httpx.AsyncClient(timeout=timeout_s, verify=cfg["tls_verify"]) as client:
        res = await client.post(
            cfg["feed_url"],
            json={"task_id": task_id},
            headers=_headers(cfg),
        )
    if res.status_code >= 400:
        raise RuntimeError(f"Activity feed hydrate HTTP {res.status_code}: {res.text[:400]}")
    data = res.json()
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RuntimeError("Activity feed hydrate returned non-object")
    return data
