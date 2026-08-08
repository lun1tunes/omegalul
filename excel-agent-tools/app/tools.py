"""Tool registry and uniform, JSON-safe execution results."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from .sessions import save_state

logger = logging.getLogger(__name__)
TOOL_FUNCS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {}
TOOL_SCHEMAS: list[dict[str, Any]] = []
CACHEABLE_TOOLS = frozenset({"workbook_introspect", "detect_tables"})
MAX_TOOL_CACHE_ENTRIES = 64


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


def _cache_key(state: dict[str, Any], tool_name: str, args: dict[str, Any]) -> str:
    """Bind cached discovery to the exact uploaded file and canonical arguments."""
    payload = {
        "file_hash": state.get("file_hash"),
        "file_size": state.get("file_size"),
        "tool": tool_name,
        "args": args,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _cached_result(state: dict[str, Any], tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name not in CACHEABLE_TOOLS:
        return None
    cached = state.get("tool_cache", {}).get(_cache_key(state, tool_name, args))
    if not isinstance(cached, dict) or cached.get("tool") != tool_name or not isinstance(cached.get("result"), dict):
        return None
    return copy.deepcopy(cached["result"])


def _store_cached_result(state: dict[str, Any], tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
    if tool_name not in CACHEABLE_TOOLS:
        return
    cache = state.setdefault("tool_cache", {})
    if not isinstance(cache, dict):
        cache = state["tool_cache"] = {}
    # A session normally has only a handful of discovery variants. Keep a hard
    # bound so model retries cannot make persisted state grow without limit.
    if len(cache) >= MAX_TOOL_CACHE_ENTRIES:
        cache.pop(next(iter(cache)))
    cache[_cache_key(state, tool_name, args)] = {
        "tool": tool_name,
        "result": copy.deepcopy(result),
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
        result = _cached_result(state, tool_name, args)
        if result is None:
            result = TOOL_FUNCS[tool_name](context, args)
            _store_cached_result(state, tool_name, args, result)
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
