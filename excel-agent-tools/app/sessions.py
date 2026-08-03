"""Disk-backed session storage for the deterministic Excel tools service."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SESSION_RE = re.compile(r"^sess_[A-Za-z0-9_-]{12,60}$")
ARTIFACT_RE = re.compile(r"^(?:res|art|clr)_[A-Za-z0-9_-]{8,64}$")
_CLEANUP_LOCK = threading.Lock()


def session_root() -> Path:
    path = Path(os.getenv("SESSION_DIR", "/data/sessions")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex}"


def validate_session_id(session_id: str) -> None:
    if not SESSION_RE.fullmatch(session_id):
        raise ValueError("Invalid session_id")


def session_dir(session_id: str, *, create: bool = False) -> Path:
    validate_session_id(session_id)
    path = session_root() / session_id
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
    return path


def session_file(session_id: str, relative_path: str) -> Path:
    """Return a path below a session directory; never accept path traversal."""
    base = session_dir(session_id).resolve()
    candidate = (base / relative_path).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError("Invalid session-relative path")
    return candidate


@contextmanager
def locked_session(session_id: str):
    """Serialize state-changing tool calls for one disk-backed session.

    n8n executes calls in order, but the public API can receive concurrent requests
    for the same session. The lock prevents a stale load/save cycle from discarding
    another call's result. Docker deployments run on Linux, where ``fcntl`` locks
    are available and released automatically if the process exits.
    """
    import fcntl

    directory = session_dir(session_id)
    if not directory.is_dir():
        raise ValueError("Session not found")
    with (directory / ".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def state_path(session_id: str) -> Path:
    return session_dir(session_id) / "state.json"


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def load_state(session_id: str) -> dict[str, Any]:
    validate_session_id(session_id)
    path = state_path(session_id)
    if not path.is_file():
        raise ValueError("Session not found")
    try:
        with path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Session state is unavailable") from error
    if state.get("session_id") != session_id:
        raise ValueError("Session state is invalid")
    return state


def save_state(session_id: str, state: dict[str, Any]) -> None:
    validate_session_id(session_id)
    if state.get("session_id") != session_id:
        raise ValueError("State belongs to another session")
    directory = session_dir(session_id)
    if not directory.is_dir():
        raise ValueError("Session not found")

    state["updated_at"] = utcnow()
    target = state_path(session_id)
    fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2, default=str)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def init_state(
    *,
    session_id: str,
    file_path: str,
    file_name: str,
    file_hash: str,
    file_size: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
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
        "tool_history": [],
    }
    save_state(session_id, state)
    return state


def cleanup_expired_sessions() -> int:
    """Best-effort TTL cleanup, called while creating a new session.

    Only directories with a valid generated session id are ever deleted.
    """
    # Uploads schedule this work in FastAPI's threadpool. Under a burst, retain
    # one bounded scan rather than starting an O(number_of_sessions) scan per
    # request. A later upload will retry if this pass is still in progress.
    if not _CLEANUP_LOCK.acquire(blocking=False):
        return 0
    try:
        return _cleanup_expired_sessions()
    finally:
        _CLEANUP_LOCK.release()


def _cleanup_expired_sessions() -> int:
    try:
        ttl_hours = int(os.getenv("SESSION_TTL_HOURS", "24"))
    except ValueError:
        ttl_hours = 24
    if ttl_hours <= 0:
        return 0

    threshold = datetime.now(UTC) - timedelta(hours=ttl_hours)
    deleted = 0
    for directory in session_root().iterdir():
        if not directory.is_dir() or not SESSION_RE.fullmatch(directory.name):
            continue
        try:
            with (directory / "state.json").open("r", encoding="utf-8") as file:
                created_at = datetime.fromisoformat(json.load(file)["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            if created_at < threshold:
                shutil.rmtree(directory)
                deleted += 1
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            # Corrupt/incomplete sessions must not make new uploads fail. Leave them for an operator.
            continue
    return deleted
