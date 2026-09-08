from __future__ import annotations

import json
from pathlib import Path

import pytest

from mas_agent_kit import SessionNotFound, SessionStore, ToolError, ToolRegistry


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(prefix="tst", root=tmp_path / "sessions", ttl_hours=24)


def _registry(store: SessionStore) -> ToolRegistry:
    tools = ToolRegistry(store, cacheable={"inventory"}, cache_key=lambda s: s.get("file_hash"))
    calls = {"inventory": 0}

    @tools.tool("inventory", "Инвентарь", {})
    def inventory(ctx, args):
        calls["inventory"] += 1
        return {"tables": [ctx.state["session_id"]]}

    @tools.tool("pick", "Выбрать", {"table_id": {"type": "string"}, "columns": {"type": ["array", "string"]}, "mapping": {"type": "object"}}, required=["table_id"])
    def pick(ctx, args):
        if not args.get("table_id"):
            raise ToolError("spec_incomplete", "Укажи table_id.", missing=["table_id"], available_tables=["t1"])
        return {"picked": args["table_id"], "columns": args.get("columns"), "mapping": args.get("mapping")}

    @tools.tool("boom", "Падает", {})
    def boom(ctx, args):
        raise RuntimeError("secret path /srv/x")

    @tools.tool("fix", "Фиксирует результат", {})
    def fix(ctx, args):
        tools.result_guard(ctx.state)
        ctx.state["result"] = {"status": "completed", "message": "Готово."}
        return {"status": "completed"}

    tools.calls_seen = calls  # type: ignore[attr-defined]
    return tools


def test_error_envelope_never_carries_the_n8n_failure_key(store: SessionStore) -> None:
    """CASE-6a9ffb35-bebdb8: n8n 2.30.8 re-runs a retryOnFail node whose first item has a truthy
    ``json.error`` — one LLM ``ask_engineer`` reached the service three times, 2 s apart, and the
    developer log counted three ``result_already_stored`` warnings. Envelopes use ``code``."""
    tools = _registry(store)
    state = store.create({"file_hash": "h1"})
    for envelope in (
        tools.run(state, "pick", {}),
        tools.run(state, "nope", {}),
        tools.run(state, "boom", {}),
        tools.run(state, "pick", "not-a-dict"),  # type: ignore[arg-type]
        ToolError("x", "y", error="smuggled").envelope(),
    ):
        assert envelope["ok"] is False and envelope["code"]
        assert "error" not in envelope, envelope


def test_registry_envelopes_are_flat_and_history_is_saved(store: SessionStore) -> None:
    tools = _registry(store)
    state = store.create({"file_hash": "h1"})
    ok = tools.run(state, "pick", {"table_id": "t1", "columns": '["a","b"]', "mapping": {"0": "x"}})
    assert ok == {"ok": True, "picked": "t1", "columns": ["a", "b"], "mapping": {"0": "x"}}
    bad = tools.run(state, "pick", {})
    assert bad["ok"] is False and bad["code"] == "spec_incomplete" and bad["missing"] == ["table_id"] and bad["available_tables"] == ["t1"]
    unknown = tools.run(state, "nope", {})
    assert unknown["code"] == "unknown_tool" and "pick" in unknown["available_tools"]
    crashed = tools.run(state, "boom", {})
    assert crashed["code"] == "tool_failed" and "/srv" not in json.dumps(crashed)
    assert tools.run(state, "pick", "not-a-dict")["code"] == "invalid_arguments"  # type: ignore[arg-type]
    reloaded = store.load(state["session_id"])
    assert [h["tool"] for h in reloaded["tool_history"]] == ["pick", "pick", "boom"]
    assert reloaded["tool_history"][1]["error_code"] == "spec_incomplete"


def test_n8n_object_shaped_arrays_and_empty_json_strings(store: SessionStore) -> None:
    tools = _registry(store)
    state = store.create({})
    res = tools.run(state, "pick", {"table_id": "t1", "columns": {"0": "a", "1": "b"}, "mapping": ""})
    assert res["columns"] == ["a", "b"] and res["mapping"] is None


def test_cache_repeat_and_result_guards(store: SessionStore) -> None:
    tools = _registry(store)
    state = store.create({"file_hash": "h1"})
    assert tools.run(state, "inventory", {}) == {"ok": True, "tables": [state["session_id"]]}
    tools.run(state, "inventory", {})
    assert tools.calls_seen["inventory"] == 1, "cacheable tool is computed once per file hash"  # type: ignore[attr-defined]
    with pytest.raises(ToolError) as exc:
        tools.repeat_guard(state, "inventory", limit=2)
    assert exc.value.code == "too_many_attempts"
    assert tools.run(state, "fix", {})["ok"] is True
    blocked = tools.run(state, "fix", {})
    assert blocked["code"] == "result_already_stored" and blocked["status"] == "completed"


def test_session_store_lifecycle_and_path_safety(store: SessionStore) -> None:
    state = store.create({"case_id": "CASE-1"})
    sid = state["session_id"]
    assert sid.startswith("tst_") and store.exists(sid)
    with store.lock(sid):
        loaded = store.load(sid)
        loaded["n"] = 1
        store.save(loaded)
    assert store.load(sid)["n"] == 1
    assert store.file(sid, "input.xlsx").parent == store.dir(sid)
    with pytest.raises(ValueError):
        store.file(sid, "../escape")
    with pytest.raises(SessionNotFound):
        store.load("tst_" + "0" * 32)
    with pytest.raises(SessionNotFound):
        store.load("../../etc/passwd")
    with pytest.raises(SessionNotFound):
        store.save({"session_id": "tst_" + "1" * 32})  # unknown id
    assert store.close(sid) == {"ok": True, "closed": True, "session_id": sid}
    assert store.close(sid)["closed"] is False
    assert store.close("garbage")["closed"] is False


def test_cleanup_removes_only_expired_sessions(tmp_path: Path) -> None:
    store = SessionStore(prefix="tst", root=tmp_path, ttl_hours=1)
    old = store.create({"created_at": "2000-01-01T00:00:00+00:00"})
    fresh = store.create({})
    assert store.exists(old["session_id"]) is False, "create() sweeps expired sessions"
    assert store.exists(fresh["session_id"])
    (tmp_path / "not-a-session").mkdir()
    assert store.cleanup() == 0
