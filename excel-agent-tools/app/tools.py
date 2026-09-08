"""Excel tool registry — the kit registry bound to the Excel session store.

Tools are plain functions ``fn(ctx, args) -> dict`` registered with ``@tool(schema)``; the n8n AI Agent
calls them through ``POST /agent-tools/{name}``. Discovery tools are cached per uploaded file
(``file_hash`` + ``file_size``) so model retries do not re-scan the workbook.

Envelope (same for every MAS agent): ``{"ok": true, ...result}`` or
``{"ok": false, "error": "<code>", "message": "<hint for the LLM>", ...details}``.
"""
from __future__ import annotations

from typing import Any

from mas_agent_kit import ToolError, ToolRegistry  # noqa: F401  (ToolError re-exported for the tool modules)

from .sessions import STORE

TOOLS = ToolRegistry(
    STORE,
    cacheable=("workbook_introspect", "detect_tables"),
    cache_key=lambda state: [state.get("file_hash"), state.get("file_size")],
)
tool = TOOLS.tool
TOOL_FUNCS = TOOLS.funcs


def execute_tool(state: dict[str, Any], tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return TOOLS.run(state, tool_name, args)
