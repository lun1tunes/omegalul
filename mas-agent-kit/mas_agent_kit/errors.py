"""One error envelope for every tool of every agent.

A tool that cannot do what the LLM asked raises ``ToolError``. The registry turns it into

    {"ok": false, "code": "<code>", "message": "<hint for the LLM>", ...details}

Codes are snake_case and stable (``spec_incomplete``, ``column_not_found``, ``question_not_human``,
``too_many_attempts``, ``result_already_stored``, ``unknown_tool``, ``tool_failed``). Details such as
``available_columns`` / ``where_to_find`` / ``missing`` sit at the top level so the model sees them
right away. These envelopes are for the LLM — the engineer is asked only through ``ask_engineer``.

The key is ``code``, never ``error``: n8n 2.30.8 treats a node output whose first item has a truthy
``json.error`` as a failed run and, with ``retryOnFail`` on the tool node, re-sends the same HTTP call
(``maxTries`` times, ``waitBetweenTries`` apart) before handing the envelope to the model
(CASE-6a9ffb35-bebdb8: one ``ask_engineer`` from Qwen → three ``result_already_stored`` warnings).
"""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """Argument or protocol problem the LLM can fix by calling the tool differently."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, **extra: Any):
        self.code = str(code)
        self.message = str(message)
        self.details: dict[str, Any] = {**(details or {}), **extra}
        super().__init__(self.message)

    def envelope(self) -> dict[str, Any]:
        return error_envelope(self.code, self.message, **self.details)


class SessionNotFound(KeyError):
    """The session id is unknown, expired or malformed. Routers map it to HTTP 404."""

    def __init__(self, session_id: str = ""):
        super().__init__("session_not_found")
        self.session_id = str(session_id or "")

    def __str__(self) -> str:  # KeyError quotes its message; keep it plain for HTTP details
        return "session_not_found"


ENVELOPE_RESERVED_KEY = "error"  # n8n's node-failure marker — a tool envelope must never carry it


def error_envelope(code: str, message: str, **details: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": False, "code": str(code), "message": str(message)}
    for key, value in details.items():
        if key not in body and key != ENVELOPE_RESERVED_KEY:
            body[key] = value
    return body
