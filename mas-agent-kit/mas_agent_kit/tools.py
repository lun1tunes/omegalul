"""Tool registry: ``@tools.tool(...)`` functions the n8n AI Agent calls through ``POST /agent-tools/{name}``.

    tools = ToolRegistry(store)

    @tools.tool("inspect_well", "Осмотреть одну скважину", {"well": {"type": "string"}}, required=["well"])
    def inspect_well(ctx: ToolContext, args: dict) -> dict:
        well = find(ctx.state, args["well"])
        if well is None:
            raise ToolError("well_not_found", "Скважина не найдена; имена — из inspect_schedule.", well=args["well"])
        return {"well": well}

    tools.run(state, "inspect_well", {"well": "P1"})   # → {"ok": true, "well": {...}}

``run`` returns the flat envelope (``{"ok": true, ...result}`` or ``{"ok": false, "error", "message", ...}``),
coerces JSON-typed arguments the LLM sent as strings, keeps a bounded ``tool_history`` in the state
and saves the state through the store. Guards: ``repeat_guard`` (``too_many_attempts``) and
``result_guard`` (``result_already_stored``) are explicit calls, so every agent decides its own protocol.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from typing import Any

from .errors import ToolError, error_envelope
from .text import parse_jsonish

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 100
MAX_CACHE_ENTRIES = 64


def tool_schema(name: str, description: str, properties: dict[str, Any] | None = None, required: Iterable[str] = ()) -> dict[str, Any]:
    """OpenAI-style function schema; ``properties`` are JSON-schema fragments per argument."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(properties or {}),
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


class ToolContext:
    """What a tool receives besides its arguments. ``ctx["state"]`` works too (older tools)."""

    def __init__(self, state: dict[str, Any], registry: "ToolRegistry"):
        self.state = state
        self.session_id = str(state.get("session_id") or "")
        self.registry = registry

    def __getitem__(self, key: str) -> Any:
        if key == "state":
            return self.state
        if key == "session_id":
            return self.session_id
        raise KeyError(key)


ToolFunc = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(
        self,
        store: Any | None = None,
        *,
        cacheable: Iterable[str] = (),
        cache_key: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.store = store
        self._funcs: dict[str, ToolFunc] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self.cacheable = frozenset(cacheable)
        self._cache_key = cache_key

    # -- registration ---------------------------------------------------------------------------

    def tool(
        self,
        name_or_schema: str | dict[str, Any],
        description: str = "",
        properties: dict[str, Any] | None = None,
        required: Iterable[str] = (),
    ) -> Callable[[ToolFunc], ToolFunc]:
        schema = name_or_schema if isinstance(name_or_schema, dict) else tool_schema(name_or_schema, description, properties, required)
        name = str(schema.get("function", {}).get("name") or "")
        if not name:
            raise ValueError("Tool schema must contain function.name")

        def decorator(fn: ToolFunc) -> ToolFunc:
            if name in self._funcs:
                raise ValueError(f"Duplicate tool registration: {name}")
            self._funcs[name] = fn
            self._schemas[name] = schema
            return fn

        return decorator

    def add(self, name: str, fn: ToolFunc, description: str = "", properties: dict[str, Any] | None = None, required: Iterable[str] = ()) -> None:
        self.tool(name, description, properties, required)(fn)

    @property
    def names(self) -> list[str]:
        return list(self._funcs)

    @property
    def funcs(self) -> dict[str, ToolFunc]:
        """Live name → function map (tests monkeypatch entries here)."""
        return self._funcs

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas.values())

    def __contains__(self, name: object) -> bool:
        return name in self._funcs

    # -- execution ------------------------------------------------------------------------------

    def run(self, state: dict[str, Any], name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        if name not in self._funcs:
            return error_envelope("unknown_tool", f"Инструмента {name} нет.", available_tools=self.names)
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return error_envelope("invalid_arguments", "Аргументы инструмента должны быть объектом.")
        args = self.coerce_args(name, args)
        ctx = ToolContext(state, self)
        try:
            result = self._cached(state, name, args)
            if result is None:
                result = self._funcs[name](ctx, args)
                self._store_cached(state, name, args, result)
            self._remember(state, name, ok=True)
            return {"ok": True, **(result if isinstance(result, dict) else {"result": result})}
        except ToolError as error:
            self._remember(state, name, ok=False, code=error.code)
            return error.envelope()
        except Exception:  # never leak paths / stack traces to the model
            logger.exception("tool %s failed", name)
            self._remember(state, name, ok=False, code="tool_failed")
            return error_envelope("tool_failed", "Инструмент завершился с внутренней ошибкой; попробуй другой путь или сообщи об этом в итоге.")

    def coerce_args(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """JSON typed as object/array but sent as text or as ``{"0": …, "1": …}`` (n8n quirk) → real values."""
        props = self._schemas.get(name, {}).get("function", {}).get("parameters", {}).get("properties", {})
        out = dict(args)
        for key, spec in props.items():
            if key not in out or not isinstance(spec, dict):
                continue
            types = spec.get("type")
            types = set(types) if isinstance(types, list) else {types}
            value = out[key]
            if isinstance(value, str) and types & {"object", "array"}:
                parsed = parse_jsonish(value)
                if parsed is not None:
                    value = parsed
                elif not value.strip():
                    out.pop(key)
                    continue
            if isinstance(value, dict) and "array" in types and value and all(str(k).isdigit() for k in value):
                value = [value[k] for k in sorted(value, key=lambda k: int(k))]
            out[key] = value
        return out

    # -- guards ---------------------------------------------------------------------------------

    @staticmethod
    def calls(state: dict[str, Any], name: str) -> int:
        history = state.get("tool_history") if isinstance(state.get("tool_history"), list) else []
        return sum(1 for item in history if isinstance(item, dict) and item.get("tool") == name)

    def repeat_guard(self, state: dict[str, Any], name: str, limit: int = 3, hint: str = "") -> None:
        n = self.calls(state, name)
        if n >= limit:
            raise ToolError(
                "too_many_attempts",
                f"{name} уже вызывался {n} раза. " + (hint or "Если данных нет — спроси инженера через ask_engineer, иначе заверши ответ."),
            )

    @staticmethod
    def result_guard(state: dict[str, Any], *, key: str = "result", when: Callable[[dict[str, Any]], bool] | None = None) -> None:
        """One stored result per session: once fixed, further result-producing calls are refused."""
        stored = state.get(key) if isinstance(state.get(key), dict) else None
        if not stored or not stored.get("status"):
            return
        if when is not None and not when(stored):
            return
        raise ToolError(
            "result_already_stored",
            "Результат уже зафиксирован в сессии (" + str(stored.get("status") or "") + "): "
            + str(stored.get("message") or "")[:300]
            + " Больше инструменты не вызывай — заверши ответ одним предложением.",
            status=str(stored.get("status") or ""),
        )

    # -- internals ------------------------------------------------------------------------------

    def _remember(self, state: dict[str, Any], name: str, *, ok: bool, code: str = "") -> None:
        history = state.get("tool_history") if isinstance(state.get("tool_history"), list) else []
        entry: dict[str, Any] = {"tool": name, "ok": ok}
        if code:
            entry["error_code"] = code
        history.append(entry)
        state["tool_history"] = history[-HISTORY_LIMIT:]
        if self.store is not None and state.get("session_id"):
            self.store.save(state)

    def _cache_id(self, state: dict[str, Any], name: str, args: dict[str, Any]) -> str:
        binding = self._cache_key(state) if self._cache_key else state.get("session_id")
        payload = json.dumps({"bind": binding, "tool": name, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cached(self, state: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        if name not in self.cacheable:
            return None
        cache = state.get("tool_cache") if isinstance(state.get("tool_cache"), dict) else {}
        hit = cache.get(self._cache_id(state, name, args))
        if not isinstance(hit, dict) or hit.get("tool") != name or not isinstance(hit.get("result"), dict):
            return None
        return copy.deepcopy(hit["result"])

    def _store_cached(self, state: dict[str, Any], name: str, args: dict[str, Any], result: Any) -> None:
        if name not in self.cacheable or not isinstance(result, dict):
            return
        cache = state.get("tool_cache") if isinstance(state.get("tool_cache"), dict) else {}
        if len(cache) >= MAX_CACHE_ENTRIES:
            cache.pop(next(iter(cache)))
        cache[self._cache_id(state, name, args)] = {"tool": name, "result": copy.deepcopy(result)}
        state["tool_cache"] = cache
