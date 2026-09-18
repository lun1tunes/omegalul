"""What the LLM sees: valid JSON, size-bounded, never a clipped string.

n8n ``clip(body, 8000)`` cut ``get_keyword`` mid-JSON (WCONHIST ~22K) so Qwen got
``…`` instead of ``details.parameters``. Bound here, stringify in the loop as-is.
"""

from __future__ import annotations

import copy
import json
from typing import Any

TOOL_VIEW_LIMIT = 32_000
_DROP_ALWAYS = frozenset({"methods"})
_DROP_IF_LARGE = frozenset({"layout", "examples", "schema_id", "schema_revision"})
_KEEP_LISTS = frozenset({"parameters", "fields", "variants", "issues", "requests", "options", "cards"})


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _drop_keys(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: _drop_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_drop_keys(item, keys) for item in value]
    return value


def _cap_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - 1] + "…"
    if isinstance(value, dict):
        return {k: _cap_strings(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_cap_strings(item, limit) for item in value]
    return value


def _trim_lists(value: Any, *, keep: frozenset[str], cap: int = 40, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _trim_lists(v, keep=keep, cap=cap, key=k) for k, v in value.items()}
    if isinstance(value, list):
        items = [_trim_lists(item, keep=keep, cap=cap) for item in value]
        if key in keep or len(items) <= cap:
            return items
        return items[:cap] + [{"_truncated": True, "omitted": len(items) - cap}]
    return value


def _slim_keyword_tree(value: Any) -> Any:
    """``get_keyword`` repeats the same parameters in fields / details.parameters / variants."""
    if isinstance(value, dict):
        out = {k: _slim_keyword_tree(v) for k, v in value.items()}
        details = out.get("details")
        if isinstance(details, dict) and details.get("variants"):
            out.pop("fields", None)
            details.pop("parameters", None)
        return out
    if isinstance(value, list):
        return [_slim_keyword_tree(item) for item in value]
    return value


def _drop_descriptions(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_descriptions(v) for k, v in value.items() if k != "description"}
    if isinstance(value, list):
        return [_drop_descriptions(item) for item in value]
    return value


def tool_model_view(result: dict[str, Any] | None, *, limit: int = TOOL_VIEW_LIMIT) -> dict[str, Any]:
    """Copy of a tool envelope that ``json.dumps`` always parses back.

    Drops unused catalogue keys, then (only if still over ``limit``) layout/examples
    and long strings. ``parameters`` / ``fields`` lists are kept intact until the
    last resort list-trim of unrelated arrays.
    """
    if not isinstance(result, dict):
        return {"ok": True, "value": result}
    view: Any = _slim_keyword_tree(_drop_keys(copy.deepcopy(result), _DROP_ALWAYS))
    if len(_dumps(view)) <= limit:
        return view
    view = _drop_keys(view, _DROP_IF_LARGE)
    if len(_dumps(view)) <= limit:
        return view
    for width in (400, 160, 80):
        view = _cap_strings(view, width)
        if len(_dumps(view)) <= limit:
            return view
    view = _drop_descriptions(view)
    if len(_dumps(view)) <= limit:
        return view
    view = _trim_lists(view, keep=_KEEP_LISTS)
    if len(_dumps(view)) <= limit:
        return view
    keyword = view.get("keyword")
    name = keyword.get("keyword") if isinstance(keyword, dict) else keyword
    return {
        "ok": view.get("ok", True),
        "code": "tool_view_truncated",
        "message": "Ответ инструмента сокращён. Вызови get_keyword с одним именем.",
        "keyword": name,
    }
