"""HTTP tool nodes for n8n 2.30.8 AI Agents (field-safe).

Why this exists: since AI Agent v3 n8n runs tool calls through the workflow engine, which
requires the tool node to have an ``execute`` method.  The old langchain node
``@n8n/n8n-nodes-langchain.toolHttpRequest`` is hidden in 2.30.8 and only has ``supplyData`` —
a tool call fails at runtime with «has a "supplyData" method but no "execute" method».
The supported node is the regular HTTP Request marked usable-as-tool
(``n8n-nodes-base.httpRequestTool``); LLM-filled arguments are declared with ``$fromAI(...)``.

Field spec per tool argument: ``(key, type, required, description)`` with type in
``string | number | boolean | json``.  ``$fromAI`` treats a value with a default as optional;
``json`` values must be non-empty, so optional JSON arguments are declared as strings
(the FastAPI side parses JSON text) — see ``fromai_call``.
"""

from __future__ import annotations

FROM_AI_TYPES = {"string", "number", "boolean", "json"}


def _fromai_text(text: str) -> str:
    # $fromAI arguments are parsed by n8n's own tokenizer: keep them double-quoted and free of
    # double quotes / backslashes so descriptions with commas, parentheses and «» stay intact.
    return str(text or "").replace("\\", " ").replace('"', "'").strip()


def fromai_call(key: str, typ: str, required: bool, description: str) -> str:
    typ = typ if typ in FROM_AI_TYPES else "string"
    desc = _fromai_text(description)
    if required:
        return f'$fromAI("{key}", "{desc}", "{typ}")'
    if typ == "json":
        # Optional JSON: n8n validates json arguments as non-empty, so an omitted value would
        # fail validation. Accept JSON text instead and let the service parse it.
        return f'$fromAI("{key}", "{_fromai_text("JSON-текст. " + desc)}", "string", "")'
    default = {"number": "0", "boolean": "false"}.get(typ, "")
    return f'$fromAI("{key}", "{desc}", "{typ}", "{default}")'


def http_request_tool_params(
    tool_name: str,
    description: str,
    fields: list[tuple[str, str, bool, str]],
    *,
    url_expr: str,
    session_expr: str,
    timeout_ms: int = 180000,
) -> dict:
    """Parameters for an ``n8n-nodes-base.httpRequestTool`` (typeVersion 4.4) node.

    Body: ``{session_id: <session_expr>, <key>: $fromAI(...), ...}`` as a JSON expression.
    """
    parts = [f"session_id: {session_expr}"]
    for key, typ, required, desc in fields:
        parts.append(f"{key}: {fromai_call(key, typ, required, desc)}")
    body = "={{ ({" + ", ".join(parts) + "}) }}"
    return {
        "toolDescription": description,
        "descriptionType": "manual",
        "method": "POST",
        "url": url_expr,
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": body,
        "options": {
            "timeout": timeout_ms,
            "response": {"response": {"fullResponse": False, "responseFormat": "json"}},
        },
    }


HTTP_REQUEST_TOOL_TYPE = "n8n-nodes-base.httpRequestTool"
HTTP_REQUEST_TOOL_VERSION = 4.4
