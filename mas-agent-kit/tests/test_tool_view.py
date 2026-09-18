"""tool_model_view: valid JSON for the agent loop, never a character-clipped string."""

from __future__ import annotations

import json

from mas_agent_kit.view import TOOL_VIEW_LIMIT, tool_model_view


def test_tool_model_view_keeps_parameters_on_large_keyword() -> None:
    """Append tool result used to clip at 8000 chars and break get_keyword JSON (WCONHIST ~22K)."""
    params = [
        {"name": f"F{i}", "type": "string", "required": i < 3, "description": "поле " * 40}
        for i in range(80)
    ]
    raw = {
        "ok": True,
        "keyword": {
            "keyword": "WCONHIST",
            "description": "История скважины",
            "fields": params,
            "details": {
                "parameters": params,
                "variants": [{"variant": "default", "parameters": params, "layout": {"newline": "LF"}}],
            },
            "methods": [{"name": "create_record", "input_schema": {"type": "object"}}],
            "examples": ["x" * 500],
        },
    }
    dumped = json.dumps(raw, ensure_ascii=False)
    assert len(dumped) > 8000
    view = tool_model_view(raw)
    encoded = json.dumps(view, ensure_ascii=False)
    parsed = json.loads(encoded)
    assert parsed["ok"] is True
    item = parsed["keyword"]
    assert item["keyword"] == "WCONHIST"
    assert "methods" not in item
    names = [row["name"] for row in item["details"]["variants"][0]["parameters"]]
    assert names[0] == "F0" and names[-1] == "F79"
    assert len(encoded) <= TOOL_VIEW_LIMIT


def test_tool_model_view_small_envelope_unchanged() -> None:
    raw = {"ok": True, "status": "completed"}
    assert tool_model_view(raw) == raw


def test_tool_model_view_always_under_limit() -> None:
    blob = {"ok": True, "payload": {"text": "я" * (TOOL_VIEW_LIMIT * 2), "items": list(range(200))}}
    view = tool_model_view(blob)
    encoded = json.dumps(view, ensure_ascii=False)
    json.loads(encoded)
    assert len(encoded) <= TOOL_VIEW_LIMIT
