"""Tool registry and uniform, JSON-safe execution results."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .sessions import save_state

logger = logging.getLogger(__name__)
TOOL_FUNCS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {}
TOOL_SCHEMAS: list[dict[str, Any]] = []


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def tool(schema: dict[str, Any]):
    def decorator(fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]):
        function = schema.get("function", {})
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Tool schema must contain function.name")
        if name in TOOL_FUNCS:
            raise ValueError(f"Duplicate tool registration: {name}")
        TOOL_FUNCS[name] = fn
        TOOL_SCHEMAS.append(schema)
        return fn

    return decorator


def tool_error(tool_name: str, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool_name,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def execute_tool(state: dict[str, Any], tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name not in TOOL_FUNCS:
        return tool_error(
            tool_name,
            "UNKNOWN_TOOL",
            f"Tool {tool_name} not found",
            {"available_tools": list(TOOL_FUNCS)},
        )
    if not isinstance(args, dict):
        return tool_error(tool_name, "INVALID_ARGUMENTS", "Tool arguments must be an object")

    context = {"state": state, "session_id": state["session_id"]}
    try:
        result = TOOL_FUNCS[tool_name](context, args)
        state.setdefault("tool_history", []).append({"tool": tool_name, "ok": True})
        # Bound persisted history; it is diagnostics, not an audit log.
        state["tool_history"] = state["tool_history"][-100:]
        save_state(state["session_id"], state)
        return {"ok": True, "tool": tool_name, "result": result}
    except ToolError as error:
        state.setdefault("tool_history", []).append(
            {"tool": tool_name, "ok": False, "error_code": error.code}
        )
        state["tool_history"] = state["tool_history"][-100:]
        save_state(state["session_id"], state)
        return tool_error(tool_name, error.code, error.message, error.details)
    except Exception:  # Do not disclose server paths or stack traces to the model/client.
        logger.exception("Unexpected error in tool %s", tool_name)
        state.setdefault("tool_history", []).append(
            {"tool": tool_name, "ok": False, "error_code": "TOOL_EXECUTION_ERROR"}
        )
        state["tool_history"] = state["tool_history"][-100:]
        save_state(state["session_id"], state)
        return tool_error(tool_name, "TOOL_EXECUTION_ERROR", "Tool execution failed")
