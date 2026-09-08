"""HTTP client for MAS Activity (``:8200``) — the only service an agent talks to besides its own tools.

    activity = ActivityClient.from_task(agent_task, agent_id="schedule_builder")
    state = activity.case_state()                       # GET  /cases/{id}/state → state
    data, name = activity.download("schedule_source")   # GET  /cases/{id}/artifacts/{artifact_id}
    activity.progress("Читаю baseline: 14 скважин")    # POST /cases/{id}/events (agent.progress)
    card = activity.upload("report", "report.xlsx", b"…", mime)   # POST /cases/{id}/artifacts → card
    activity.finish_task(result)                        # POST /cases/{id}/run  resume source=agent (long jobs)

Event posting never raises: a dead Activity must not break the agent. Standard library only —
the field service has pip wheels but no extra HTTP stack requirement.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

EVENT_KINDS = ("agent.accepted", "agent.progress", "agent.failed")


class ActivityClient:
    def __init__(
        self,
        base_url: str,
        *,
        agent_id: str,
        case_id: str = "",
        task_id: str = "",
        timeout: float = 15.0,
        headers: dict[str, str] | None = None,
    ):
        self.base_url = str(base_url or "").rstrip("/")
        self.agent_id = str(agent_id or "")
        self.case_id = str(case_id or "")
        self.task_id = str(task_id or "")
        self.timeout = timeout
        self.headers = dict(headers or {})

    @classmethod
    def from_task(cls, task: dict[str, Any], *, agent_id: str, default_base_url: str = "", **kw: Any) -> "ActivityClient":
        inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
        base = str(inputs.get("activity_base_url") or default_base_url or "")
        return cls(base, agent_id=agent_id, case_id=str(task.get("case_id") or ""), task_id=str(task.get("task_id") or ""), **kw)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.case_id)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/cases/{quote(self.case_id, safe='')}{path}"

    def _request(self, method: str, url: str, *, body: bytes | None = None, content_type: str = "", timeout: float | None = None) -> tuple[bytes, dict[str, str]]:
        headers = {**self.headers}
        if content_type:
            headers["Content-Type"] = content_type
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310 — URL comes from Runtime Config
            return resp.read(), {k.lower(): v for k, v in resp.headers.items()}

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        raw, _ = self._request(method, self._url(path), body=body, content_type="application/json" if body else "", timeout=timeout)
        return json.loads(raw.decode("utf-8", errors="replace")) if raw else {}

    # -- read -----------------------------------------------------------------------------------

    def case_state(self) -> dict[str, Any]:
        """Case state (artifact cards, ``agents`` slots, ``hitl``); ``{}`` when Activity is unreachable."""
        if not self.configured:
            return {}
        try:
            packet = self._json("GET", "/state")
        except (HTTPError, URLError, OSError, ValueError) as exc:
            logger.warning("activity state unavailable case_id=%s: %s", self.case_id, exc)
            return {}
        state = packet.get("state") if isinstance(packet, dict) else None
        return state if isinstance(state, dict) else {}

    def download(self, artifact_id: str, *, timeout: float = 60.0) -> tuple[bytes, str]:
        """Artifact bytes and the filename from ``Content-Disposition`` (may be empty). Raises on HTTP errors."""
        return self.download_url(self.artifact_url(artifact_id), timeout=timeout)

    def download_url(self, url: str, *, timeout: float = 60.0) -> tuple[bytes, str]:
        """Same as ``download`` for an explicit URL (e.g. ``inputs.excel_url`` given by the orchestrator)."""
        raw, headers = self._request("GET", str(url), timeout=timeout)
        return raw, _disposition_filename(headers.get("content-disposition", ""))

    def artifact_url(self, artifact_id: str) -> str:
        return self._url(f"/artifacts/{quote(str(artifact_id), safe='')}")

    # -- events ---------------------------------------------------------------------------------

    def event(self, kind: str, message: str, *, status: str = "", payload: dict[str, Any] | None = None) -> bool:
        """Feed line for the engineer. Never raises; returns whether Activity accepted it."""
        if not self.configured:
            return False
        body: dict[str, Any] = {
            "kind": kind,
            "actor": self.agent_id,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "status_message": str(message or "")[:400],
        }
        if status:
            body["status"] = status
        if payload:
            body["payload"] = payload
        try:
            self._json("POST", "/events", body, timeout=8.0)
            return True
        except (HTTPError, URLError, OSError, ValueError) as exc:
            logger.warning("activity event failed case_id=%s kind=%s: %s", self.case_id, kind, exc)
            return False

    def accepted(self, message: str) -> bool:
        return self.event("agent.accepted", message)

    def progress(self, message: str, *, status: str = "", payload: dict[str, Any] | None = None) -> bool:
        return self.event("agent.progress", message, status=status, payload=payload)

    def failed(self, message: str) -> bool:
        return self.event("agent.failed", message, status="failed")

    # -- write ----------------------------------------------------------------------------------

    def upload(self, artifact_id: str, filename: str, content: bytes, mime_type: str = "application/octet-stream", *, summary: str = "") -> dict[str, Any]:
        """Store a binary deliverable in the case; returns the artifact card to put into ``agent_result.artifacts``."""
        boundary = "----mas-agent-kit-" + uuid.uuid4().hex
        fields = {"artifact_id": str(artifact_id), "producer": self.agent_id, "summary": summary or ""}
        parts: list[bytes] = []
        for key, value in fields.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8"))
        safe_name = str(filename or artifact_id).replace('"', "'")
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe_name}\"\r\n"
            f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8")
        )
        parts.append(content)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        raw, _ = self._request("POST", self._url("/artifacts"), body=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}", timeout=120.0)
        packet = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
        card = packet.get("artifact") if isinstance(packet, dict) else None
        if not isinstance(card, dict):
            raise ValueError("activity did not return an artifact card")
        return card

    def finish_task(self, agent_result: dict[str, Any]) -> dict[str, Any]:
        """Long job done: hand the final ``agent_result`` to the orchestrator (``resume source=agent``)."""
        payload = {
            "action": "resume",
            "source": "agent",
            "agent_id": self.agent_id,
            "task_id": str(agent_result.get("task_id") or self.task_id),
            "agent_result": agent_result,
        }
        return self._json("POST", "/run", payload, timeout=30.0)


def _disposition_filename(disposition: str) -> str:
    m = re.search(r"filename\*=UTF-8''([^;]+)", disposition or "")
    if m:
        return unquote(m.group(1).strip())
    m = re.search(r'filename="?([^";]+)"?', disposition or "")
    return m.group(1).strip() if m else ""
