"""Orchestrator invocation for HITL and task start (webhook or n8n REST)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any

import httpx

FORMAT_NODE = "Format orchestrator response"
TRIGGER_NODE = "When called by another workflow"
DEFAULT_ORCH_WF = "ba8ba59f-e4e4-5ff6-b22c-63ceae883271"

# field_name -> (filename, bytes, mime_type)
BinaryMap = dict[str, tuple[str, bytes, str]]

_cookie: str | None = None
_cookie_at = 0.0


class OrchestratorError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _cfg() -> dict[str, str]:
    return {
        "webhook_url": (os.getenv("ORCHESTRATOR_WEBHOOK_URL") or "").strip(),
        "auth_header": (os.getenv("ORCHESTRATOR_AUTH_HEADER") or "").strip(),
        "auth_value": (os.getenv("ORCHESTRATOR_AUTH_VALUE") or "").strip(),
        "n8n_base": (os.getenv("N8N_BASE_URL") or os.getenv("N8N_HOST") or "").rstrip("/"),
        "n8n_user": (os.getenv("N8N_USERNAME") or "").strip(),
        "n8n_password": (os.getenv("N8N_PASSWORD") or "").strip(),
        "workflow_id": (os.getenv("ORCHESTRATOR_WORKFLOW_ID") or DEFAULT_ORCH_WF).strip(),
        "mode": (os.getenv("HITL_MODE") or "auto").strip().lower(),
    }


def hitl_backend() -> str:
    cfg = _cfg()
    mode = cfg["mode"]
    if mode in {"local", "webhook", "n8n_rest"}:
        return mode
    if cfg["webhook_url"]:
        return "webhook"
    if cfg["n8n_base"] and cfg["n8n_user"] and cfg["n8n_password"]:
        return "n8n_rest"
    return "local"


def _deref_flat(flat: list[Any], value: Any, *, depth: int = 0) -> Any:
    if depth > 40:
        return value
    if isinstance(value, str) and value.isdigit():
        idx = int(value)
        if 0 <= idx < len(flat):
            return _deref_flat(flat, flat[idx], depth=depth + 1)
    if isinstance(value, int) and 0 <= value < len(flat) and not isinstance(flat[value], (int, float)):
        # only follow int refs when target is container/string commonly used by flatted
        target = flat[value]
        if isinstance(target, (dict, list, str)) or target is None:
            return _deref_flat(flat, target, depth=depth + 1)
    if isinstance(value, dict):
        return {k: _deref_flat(flat, v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_deref_flat(flat, v, depth=depth + 1) for v in value]
    return value


def extract_orchestrator_response(execution_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull orchestrator_response from n8n execution includeData payload."""
    data = execution_payload.get("data", execution_payload)
    inner = data.get("data")
    if isinstance(inner, str):
        try:
            flat = json.loads(inner)
        except json.JSONDecodeError:
            return None
        if not isinstance(flat, list):
            return None
        for item in flat:
            if not isinstance(item, dict):
                continue
            contract = item.get("contract")
            if contract == "orchestrator_response" or (
                isinstance(contract, str) and contract.isdigit() and flat[int(contract)] == "orchestrator_response"
            ):
                return _deref_flat(flat, item)
        return None
    if isinstance(inner, dict):
        run = ((inner.get("resultData") or {}).get("runData") or {})
        rows = run.get(FORMAT_NODE) or []
        if not rows:
            return None
        main = (((rows[-1].get("data") or {}).get("main") or [[]])[0] or [{}])
        js = (main[0] or {}).get("json") if main else None
        return js if isinstance(js, dict) else None
    return None


async def invoke_orchestrator(
    payload: dict[str, Any],
    *,
    files: BinaryMap | None = None,
    timeout_s: float = 90.0,
) -> dict[str, Any]:
    backend = hitl_backend()
    if backend == "local":
        raise OrchestratorError("Orchestrator backend not configured", status_code=503)
    if backend == "webhook":
        return await _invoke_webhook(payload, files=files, timeout_s=timeout_s)
    return await _invoke_n8n_rest(payload, files=files, timeout_s=timeout_s)


def _binary_to_n8n(files: BinaryMap | None) -> dict[str, dict[str, str]] | None:
    if not files:
        return None
    out: dict[str, dict[str, str]] = {}
    for key, (filename, content, mime) in files.items():
        out[key] = {
            "data": base64.b64encode(content).decode("ascii"),
            "mimeType": mime or "application/octet-stream",
            "fileName": filename,
        }
    return out


async def _invoke_webhook(
    payload: dict[str, Any],
    *,
    files: BinaryMap | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    cfg = _cfg()
    headers: dict[str, str] = {}
    if cfg["auth_header"] and cfg["auth_value"]:
        headers[cfg["auth_header"]] = cfg["auth_value"]
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        if files:
            form: dict[str, Any] = {}
            for key, value in payload.items():
                if value is None:
                    continue
                if isinstance(value, (dict, list)):
                    form[key] = json.dumps(value, ensure_ascii=False)
                else:
                    form[key] = str(value)
            upload = [
                (key, (filename, content, mime or "application/octet-stream"))
                for key, (filename, content, mime) in files.items()
            ]
            res = await client.post(cfg["webhook_url"], data=form, files=upload, headers=headers)
        else:
            headers = {**headers, "Content-Type": "application/json"}
            res = await client.post(cfg["webhook_url"], json=payload, headers=headers)
    if res.status_code >= 400:
        raise OrchestratorError(
            f"Orchestrator webhook HTTP {res.status_code}",
            status_code=502,
            detail=res.text[:800],
        )
    data = res.json()
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise OrchestratorError("Orchestrator webhook returned non-object", status_code=502)
    return data


async def _login(client: httpx.AsyncClient, cfg: dict[str, str]) -> str:
    global _cookie, _cookie_at
    if _cookie and (time.time() - _cookie_at) < 25 * 60:
        return _cookie
    res = await client.post(
        f"{cfg['n8n_base']}/rest/login",
        json={"emailOrLdapLoginId": cfg["n8n_user"], "password": cfg["n8n_password"]},
    )
    if res.status_code >= 400:
        raise OrchestratorError("n8n login failed", status_code=502, detail=res.text[:400])
    jar = "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)
    _cookie = jar or (res.headers.get("set-cookie") or "").split(";")[0]
    _cookie_at = time.time()
    return _cookie


async def _invoke_n8n_rest(
    payload: dict[str, Any],
    *,
    files: BinaryMap | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    cfg = _cfg()
    if not (cfg["n8n_base"] and cfg["n8n_user"] and cfg["n8n_password"]):
        raise OrchestratorError("N8N_BASE_URL/N8N_USERNAME/N8N_PASSWORD required", status_code=503)

    item: dict[str, Any] = {"json": payload}
    binary = _binary_to_n8n(files)
    if binary:
        item["binary"] = binary

    body = {
        "triggerToStartFrom": {
            "name": TRIGGER_NODE,
            "data": {
                "startTime": 1,
                "executionIndex": 0,
                "executionTime": 0,
                "source": [],
                "data": {"main": [[item]]},
            },
        },
        "destinationNode": {"nodeName": FORMAT_NODE, "mode": "inclusive"},
    }

    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        await _login(client, cfg)
        run = await client.post(
            f"{cfg['n8n_base']}/rest/workflows/{cfg['workflow_id']}/run",
            json=body,
        )
        if run.status_code >= 400:
            global _cookie_at
            _cookie_at = 0
            await _login(client, cfg)
            run = await client.post(
                f"{cfg['n8n_base']}/rest/workflows/{cfg['workflow_id']}/run",
                json=body,
            )
        if run.status_code >= 400:
            raise OrchestratorError(
                f"n8n run failed HTTP {run.status_code}",
                status_code=502,
                detail=run.text[:800],
            )
        execution_id = ((run.json() or {}).get("data") or {}).get("executionId")
        if not execution_id:
            raise OrchestratorError("n8n run missing executionId", status_code=502, detail=run.text[:400])

        deadline = time.time() + timeout_s
        last: dict[str, Any] = {}
        while time.time() < deadline:
            ex = await client.get(
                f"{cfg['n8n_base']}/rest/executions/{execution_id}",
                params={"includeData": "true"},
            )
            if ex.status_code >= 400:
                raise OrchestratorError(
                    f"n8n execution fetch HTTP {ex.status_code}",
                    status_code=502,
                    detail=ex.text[:400],
                )
            last = ex.json()
            status = (last.get("data") or last).get("status")
            if status in {"success", "error", "crashed", "canceled"}:
                if status != "success":
                    raise OrchestratorError(
                        f"Orchestrator execution {status}",
                        status_code=502,
                        detail={"execution_id": execution_id, "status": status},
                    )
                parsed = extract_orchestrator_response(last)
                if not parsed:
                    raise OrchestratorError(
                        "Could not parse orchestrator_response from execution",
                        status_code=502,
                        detail={"execution_id": execution_id},
                    )
                return parsed
            await asyncio.sleep(0.6)
        raise OrchestratorError("Orchestrator execution timed out", status_code=504, detail=last.get("data"))
