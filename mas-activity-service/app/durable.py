"""Pull durable task catalog / feed from n8n Data Table hydrate webhooks."""

from __future__ import annotations

import os
from typing import Any

import httpx


def durable_cfg() -> dict[str, str]:
    return {
        "list_url": (os.getenv("ACTIVITY_LIST_URL") or "").strip(),
        "feed_url": (os.getenv("ACTIVITY_FEED_URL") or "").strip(),
        "auth_header": (os.getenv("ACTIVITY_DURABLE_AUTH_HEADER") or "").strip(),
        "auth_value": (os.getenv("ACTIVITY_DURABLE_AUTH_VALUE") or "").strip(),
    }


def durable_enabled() -> bool:
    cfg = durable_cfg()
    return bool(cfg["list_url"] or cfg["feed_url"])


def _headers(cfg: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg["auth_header"] and cfg["auth_value"]:
        headers[cfg["auth_header"]] = cfg["auth_value"]
    return headers


async def fetch_task_list(*, timeout_s: float = 30.0) -> dict[str, Any] | None:
    cfg = durable_cfg()
    if not cfg["list_url"]:
        return None
    async with httpx.AsyncClient(timeout=timeout_s) as client:
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
    async with httpx.AsyncClient(timeout=timeout_s) as client:
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
