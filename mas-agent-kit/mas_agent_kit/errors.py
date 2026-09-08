"""One error envelope for every tool of every agent.

A tool that cannot do what the LLM asked raises ``ToolError``. The registry turns it into

    {"ok": false, "error": "<code>", "message": "<hint for the LLM>", ...details}

Codes are snake_case and stable (``spec_incomplete``, ``column_not_found``, ``question_not_human``,
``too_many_attempts``, ``result_already_stored``, ``unknown_tool``, ``tool_failed``). Details such as
``available_columns`` / ``where_to_find`` / ``missing`` sit at the top level so the model sees them
right away. These envelopes are for the LLM — the engineer is asked only through ``ask_engineer``.
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


def error_envelope(code: str, message: str, **details: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": False, "error": str(code), "message": str(message)}
    for key, value in details.items():
        if key not in body:
            body[key] = value
    return body
