"""Excel sessions on the shared kit store (disk-backed, one directory per uploaded workbook).

Thin names over ``mas_agent_kit.SessionStore`` so the tool modules and tests keep calling
``load_state`` / ``save_state`` / ``locked_session`` / ``session_file``. Ids look like ``sess_<hex>``.
"""
from __future__ import annotations

import re
from typing import Any

from mas_agent_kit import SessionStore
from mas_agent_kit.session import utcnow

ARTIFACT_RE = re.compile(r"^(?:res|art|clr)_[A-Za-z0-9_-]{8,64}$")

STORE = SessionStore(prefix="sess", app_name="excel_agent")

new_session_id = STORE.new_id
session_dir = STORE.dir
session_file = STORE.file
session_lock_path = STORE.lock_path
locked_session = STORE.lock
load_state = STORE.load
cleanup_expired_sessions = STORE.cleanup
close_session = STORE.close


def save_state(session_id: str, state: dict[str, Any]) -> None:
    if state.get("session_id") != session_id:
        raise ValueError("State belongs to another session")
    STORE.save(state)


def init_state(
    *,
    session_id: str,
    file_path: str,
    file_name: str,
    file_hash: str,
    file_size: int,
    payload: dict[str, Any],
    **case_fields: Any,
) -> dict[str, Any]:
    """First ``state.json`` of a session whose directory and input file already exist.

    ``case_fields`` are the agent-task fields (``case_id``, ``task_id``, ``objective``, ``activity_base_url``…)
    the kit helpers read from the state; the legacy upload API passes none.
    """
    now = utcnow()
    state: dict[str, Any] = {
        "session_id": session_id,
        "created_at": now,
        "updated_at": now,
        "file_path": file_path,
        "file_name": file_name,
        "file_hash": file_hash,
        "file_size": file_size,
        "status": "uploaded",
        "payload": payload,
        "workbook_meta": {},
        "tables": {},
        "result_sets": {},
        "artifacts": {},
        "clarifications": {},
        "plan": {},
        "assumptions": [],
        "warnings": [],
        "final_output": None,
        # Internal deterministic cache for expensive workbook discovery tools.
        # API state serializers must never expose it to clients or the model.
        "tool_cache": {},
        "tool_history": [],
        **case_fields,
    }
    return STORE.save(state)
