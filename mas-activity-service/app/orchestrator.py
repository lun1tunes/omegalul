"""Orchestrator invocation for HITL and task start (webhook or n8n REST)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx

from app.settings import UNCONFIGURED_N8N, Settings, get_settings

FORMAT_NODE = "Format orchestrator response"
TRIGGER_NODE = "When called by another workflow"

# field_name -> (filename, bytes, mime_type)
BinaryMap = dict[str, tuple[str, bytes, str]]

logger = logging.getLogger(__name__)

_cookie: str | None = None
_cookie_base: str | None = None
_cookie_at = 0.0
_cookie_lock = asyncio.Lock()


class OrchestratorError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class N8nClient:
    """Single production path to n8n: webhook if URL is known, otherwise REST."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def transport(self) -> str:
        return self.settings.n8n_transport

    def require_configured(self) -> None:
        if self.transport == "unconfigured":
            raise OrchestratorError(UNCONFIGURED_N8N, status_code=503)


def _cfg() -> Settings:
    return get_settings()


def hitl_backend() -> str:
    """Return ``webhook`` / ``n8n_rest`` / ``unconfigured``. There is no local mode."""
    return get_settings().n8n_transport


def orchestrator_config_summary() -> dict[str, Any]:
    """Return safe runtime configuration facts for the diagnostics endpoint."""
    settings = get_settings()
    return {
        "backend": settings.n8n_transport,
        "webhook_configured": bool(settings.resolved_orchestrator_webhook),
        "auth_configured": bool(settings.orchestrator_auth_header and settings.orchestrator_auth_value),
        "n8n_rest_configured": bool(
            settings.n8n_base and settings.n8n_username and settings.n8n_password
        ),
    }


async def probe_orchestrator_connectivity(*, timeout_s: float = 10.0) -> dict[str, Any]:
    """Safely probe the live orchestrator boundary without creating a task."""
    from app.readiness import probe_n8n_stack

    stack = await probe_n8n_stack(timeout_s=timeout_s)
    orch = stack.get("checks", {}).get("orchestrator") or {}
    return {
        **orchestrator_config_summary(),
        "checked": True,
        "ok": bool(orch.get("ok")),
        "reachable": orch.get("reachable"),
        "http_status": orch.get("http_status"),
        "error": orch.get("error"),
        "note": orch.get("note") or orch.get("error"),
        "transport": stack.get("n8n_transport"),
    }


def _deref_flat(
    flat: list[Any],
    value: Any,
    *,
    depth: int = 0,
    seen: frozenset[int] | None = None,
) -> Any:
    """Resolve n8n flatted JSON refs.

    Flatted encodes pointers as *digit strings* (\"12\"). Bare ints are real values
    (e.g. version=4) and must never be followed — doing so walks the wrong graph and
    can hang / OOM on large execution payloads.
    """
    if depth > 40:
        return value
    seen = seen or frozenset()
    if isinstance(value, str) and value.isdigit():
        idx = int(value)
        if 0 <= idx < len(flat) and idx not in seen:
            return _deref_flat(flat, flat[idx], depth=depth + 1, seen=seen | {idx})
        return value
    if isinstance(value, dict):
        return {k: _deref_flat(flat, v, depth=depth + 1, seen=seen) for k, v in value.items()}
    if isinstance(value, list):
        return [_deref_flat(flat, v, depth=depth + 1, seen=seen) for v in value]
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
    client = N8nClient()
    client.require_configured()
    action = str(payload.get("action") or "").strip() or "unknown"
    logger.info(
        "n8n invoke transport=%s action=%s task_id=%s files=%s timeout=%ss",
        client.transport,
        action,
        payload.get("task_id"),
        len(files or {}),
        timeout_s,
    )
    try:
        if client.transport == "webhook":
            result = await _invoke_webhook(payload, files=files, timeout_s=timeout_s)
        else:
            result = await _invoke_n8n_rest(payload, files=files, timeout_s=timeout_s)
    except OrchestratorError:
        logger.exception("n8n invoke failed transport=%s action=%s", client.transport, action)
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("n8n invoke failed transport=%s action=%s", client.transport, action)
        raise OrchestratorError(
            f"n8n request failed: {type(exc).__name__}: {str(exc)[:300]}",
            status_code=502,
        ) from exc
    logger.info(
        "n8n invoke ok transport=%s action=%s status=%s task_id=%s",
        client.transport,
        action,
        result.get("status"),
        result.get("task_id"),
    )
    return result


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
    settings = _cfg()
    url = settings.resolved_orchestrator_webhook
    if not url:
        raise OrchestratorError(UNCONFIGURED_N8N, status_code=503)
    headers: dict[str, str] = dict(settings.orchestrator_headers())
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s, verify=settings.httpx_verify) as client:
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
                res = await client.post(url, data=form, files=upload, headers=headers)
            else:
                headers = {**headers, "Content-Type": "application/json"}
                res = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise OrchestratorError(
            f"Orchestrator webhook request failed: {type(exc).__name__}: {str(exc)[:300]}",
            status_code=502,
        ) from exc
    logger.info(
        "n8n webhook POST %s -> %s in %.0fms",
        url.split("?", 1)[0],
        res.status_code,
        (time.perf_counter() - started) * 1000,
    )
    if res.status_code >= 400:
        raise OrchestratorError(
            f"Orchestrator webhook HTTP {res.status_code}",
            status_code=502,
            detail=res.text[:800],
        )
    try:
        data = res.json()
    except ValueError as exc:
        raise OrchestratorError(
            "Orchestrator webhook returned invalid JSON", status_code=502
        ) from exc
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise OrchestratorError("Orchestrator webhook returned non-object", status_code=502)
    return data


async def _login(client: httpx.AsyncClient, cfg: Settings) -> str:
    global _cookie, _cookie_base, _cookie_at
    async with _cookie_lock:
        if (
            _cookie
            and _cookie_base == cfg.n8n_base
            and (time.time() - _cookie_at) < 25 * 60
        ):
            # Each invocation owns a fresh AsyncClient. Re-apply the cached
            # cookie to that client's jar; returning it alone does not send it.
            for pair in _cookie.split(";"):
                name, separator, value = pair.partition("=")
                if separator and name.strip():
                    client.cookies.set(name.strip(), value.strip())
            return _cookie
        res = await client.post(
            f"{cfg.n8n_base}/rest/login",
            json={"emailOrLdapLoginId": cfg.n8n_username, "password": cfg.n8n_password},
        )
        if res.status_code >= 400:
            raise OrchestratorError("n8n login failed", status_code=502, detail=res.text[:400])
        jar = "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)
        _cookie = jar or (res.headers.get("set-cookie") or "").split(";")[0]
        _cookie_base = cfg.n8n_base
        _cookie_at = time.time()
        return _cookie


def _load_execution_data_via_postgres(execution_id: str) -> str | None:
    """Read flatted n8n execution_data from the compose Postgres (avoids n8n includeData OOM)."""
    import re
    import subprocess
    from pathlib import Path

    if not re.fullmatch(r"\d{1,18}", str(execution_id)):
        return None
    root = Path(__file__).resolve().parents[2]
    try:
        cp = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "n8n",
                "-d",
                "n8n",
                "-At",
                "-c",
                f'SELECT data FROM execution_data WHERE "executionId"={execution_id};',
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (cp.stdout or "").strip()
    if cp.returncode != 0 or not raw.startswith("["):
        return None
    return raw


async def _invoke_n8n_rest(
    payload: dict[str, Any],
    *,
    files: BinaryMap | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    cfg = _cfg()
    if not (cfg.n8n_base and cfg.n8n_username and cfg.n8n_password):
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

    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=True,
        verify=cfg.httpx_verify,
    ) as client:
        await _login(client, cfg)
        run = await client.post(
            f"{cfg.n8n_base}/rest/workflows/{cfg.orchestrator_workflow_id}/run",
            json=body,
        )
        if run.status_code >= 400:
            global _cookie_at
            _cookie_at = 0
            await _login(client, cfg)
            run = await client.post(
                f"{cfg.n8n_base}/rest/workflows/{cfg.orchestrator_workflow_id}/run",
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
        # Poll metadata only — full includeData every 0.6s OOMs this host on long orch runs.
        while time.time() < deadline:
            ex = await client.get(
                f"{cfg.n8n_base}/rest/executions/{execution_id}",
                params={"includeData": "false"},
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
                # Failed runs do not need their potentially huge execution blob.
                # More importantly, do not invoke the optional local Docker/Postgres
                # fallback for an already-failed run: in a corporate/Windows
                # deployment Docker may not exist and the fallback can block for its
                # full subprocess timeout while we only need to fail closed.
                if status != "success":
                    raise OrchestratorError(
                        _execution_error_message(last, status=status),
                        status_code=502,
                        detail={"execution_id": execution_id, "status": status},
                    )
                # Prefer Postgres for the payload: includeData of golden SCHEDULE runs
                # has repeatedly heap-OOM'd n8n (~512MiB) while serializing the response.
                pg = await asyncio.to_thread(_load_execution_data_via_postgres, str(execution_id))
                if pg is not None:
                    last = {"data": {"data": pg, "status": status}}
                else:
                    try:
                        full = await client.get(
                            f"{cfg.n8n_base}/rest/executions/{execution_id}",
                            params={"includeData": "true"},
                        )
                    except httpx.HTTPError as exc:
                        raise OrchestratorError(
                            f"n8n execution fetch failed: {exc}",
                            status_code=502,
                            detail={"execution_id": execution_id},
                        ) from exc
                    if full.status_code >= 400:
                        raise OrchestratorError(
                            f"n8n execution fetch HTTP {full.status_code}",
                            status_code=502,
                            detail=full.text[:400],
                        )
                    last = full.json()
                parsed = extract_orchestrator_response(last)
                if parsed:
                    return parsed
                raise OrchestratorError(
                    "Could not parse orchestrator_response from execution",
                    status_code=502,
                    detail={"execution_id": execution_id},
                )
            await asyncio.sleep(0.6)
        raise OrchestratorError("Orchestrator execution timed out", status_code=504, detail=last.get("data"))


def _execution_error_message(execution_payload: dict[str, Any], *, status: str) -> str:
    """Best-effort human message from a failed n8n execution payload."""
    data = execution_payload.get("data", execution_payload)
    inner = data.get("data")
    err: Any = None
    last_node = None
    if isinstance(inner, str):
        try:
            flat = json.loads(inner)
        except json.JSONDecodeError:
            flat = None
        if isinstance(flat, list):
            for item in flat:
                if isinstance(item, dict) and item.get("error") is not None and "runData" in item:
                    err = _deref_flat(flat, item.get("error"))
                    last_node = _deref_flat(flat, item.get("lastNodeExecuted"))
                    break
    elif isinstance(inner, dict):
        result = inner.get("resultData") or {}
        err = result.get("error")
        last_node = result.get("lastNodeExecuted")
    node_name = ""
    if isinstance(err, dict):
        node = err.get("node") if isinstance(err.get("node"), dict) else {}
        node_name = str(node.get("name") or last_node or "").strip()
        reason = ""
        ctx = err.get("context") if isinstance(err.get("context"), dict) else {}
        if ctx.get("outputParserFailReason"):
            reason = str(ctx["outputParserFailReason"])
        elif err.get("description"):
            reason = str(err["description"])
        elif err.get("message"):
            reason = str(err["message"])
        if node_name and reason:
            return f"Orchestrator node «{node_name}»: {reason[:400]}"
        if reason:
            return f"Orchestrator execution {status}: {reason[:400]}"
    if last_node:
        return f"Orchestrator execution {status} at «{last_node}»"
    return f"Orchestrator execution {status}"
