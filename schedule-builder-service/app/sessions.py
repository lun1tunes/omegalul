"""In-process schedule sessions. Source text stays here; the LLM never sees the .INC."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

_LOCK = threading.Lock()
_SESSIONS: dict[str, dict[str, Any]] = {}
TTL_SEC = 60 * 60


def new_session_id() -> str:
    return "sch_" + uuid.uuid4().hex


def put(state: dict[str, Any]) -> dict[str, Any]:
    sid = str(state.get("session_id") or new_session_id())
    state["session_id"] = sid
    state["updated_at"] = time.time()
    with _LOCK:
        _expire_unlocked()
        _SESSIONS[sid] = state
    return state


def get(session_id: str) -> dict[str, Any]:
    with _LOCK:
        _expire_unlocked()
        state = _SESSIONS.get(str(session_id or ""))
        if state is None:
            raise KeyError("session_not_found")
        return state


def save(state: dict[str, Any]) -> dict[str, Any]:
    sid = str(state.get("session_id") or "")
    if not sid:
        raise KeyError("session_not_found")
    state["updated_at"] = time.time()
    with _LOCK:
        _SESSIONS[sid] = state
    return state


def close(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "")
    with _LOCK:
        existed = sid in _SESSIONS
        _SESSIONS.pop(sid, None)
        _expire_unlocked()
    return {"ok": True, "closed": existed, "session_id": sid}


def _expire_unlocked() -> None:
    now = time.time()
    dead = [sid for sid, row in _SESSIONS.items() if now - float(row.get("updated_at") or 0) > TTL_SEC]
    for sid in dead:
        _SESSIONS.pop(sid, None)
