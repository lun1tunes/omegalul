"""Disk-backed sessions: one directory per session with ``state.json`` and any files the agent keeps.

The LLM never sees the files — it works through tools that read them from the session. State
survives a service restart (field: the Windows service may be restarted mid-case) and is
serialised per session with a cross-process file lock (``filelock``: msvcrt on Windows, fcntl on
Unix). Expired sessions are swept on ``create``.

    store = SessionStore(prefix="sch")                 # ids look like sch_<hex>
    state = store.create({"case_id": ..., "task_id": ...})
    with store.lock(state["session_id"]):
        state = store.load(sid); ...; store.save(state)
    store.close(sid)

Root directory: ``SESSION_DIR`` env var, else ``/data/sessions`` on Unix when writable, else the
system temp dir. Each service can pass its own ``root``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from .errors import SessionNotFound

_ID_BODY = r"[A-Za-z0-9_-]{12,60}"


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def default_session_root(app_name: str = "mas_agent") -> Path:
    env = os.getenv("SESSION_DIR")
    if env:
        return Path(env)
    if sys.platform != "win32":
        unix_default = Path("/data/sessions")
        try:
            unix_default.mkdir(parents=True, exist_ok=True)
            if os.access(unix_default, os.W_OK):
                return unix_default
        except OSError:
            pass
    return Path(tempfile.gettempdir()) / f"{app_name}_sessions"


class SessionStore:
    def __init__(self, prefix: str = "sess", root: Path | str | None = None, *, ttl_hours: int | None = None, app_name: str = ""):
        if not re.fullmatch(r"[a-z][a-z0-9]{0,15}", prefix):
            raise ValueError("session prefix must be a short lowercase identifier")
        self.prefix = prefix
        self._root = Path(root) if root else None
        self.app_name = app_name or prefix
        self._ttl_hours = ttl_hours
        self._id_re = re.compile(rf"^{re.escape(prefix)}_{_ID_BODY}$")
        self._cleanup_guard = threading.Lock()

    # -- ids and paths --------------------------------------------------------------------------

    @property
    def root(self) -> Path:
        path = (self._root or default_session_root(self.app_name)).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def ttl_hours(self) -> int:
        if self._ttl_hours is not None:
            return self._ttl_hours
        try:
            return int(os.getenv("SESSION_TTL_HOURS", "24"))
        except ValueError:
            return 24

    def new_id(self) -> str:
        return f"{self.prefix}_{uuid.uuid4().hex}"

    def is_valid_id(self, session_id: Any) -> bool:
        return isinstance(session_id, str) and bool(self._id_re.fullmatch(session_id))

    def validate(self, session_id: Any) -> str:
        if not self.is_valid_id(session_id):
            raise SessionNotFound(str(session_id or ""))
        return str(session_id)

    def dir(self, session_id: str, *, create: bool = False) -> Path:
        path = self.root / self.validate(session_id)
        if create:
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        return path

    def file(self, session_id: str, relative_path: str) -> Path:
        """A path below the session directory; path traversal is rejected."""
        base = self.dir(session_id).resolve()
        candidate = (base / relative_path).resolve()
        if candidate != base and base not in candidate.parents:
            raise ValueError("Invalid session-relative path")
        return candidate

    def exists(self, session_id: Any) -> bool:
        return self.is_valid_id(session_id) and (self.root / str(session_id) / "state.json").is_file()

    def lock_path(self, session_id: str) -> Path:
        """Lock files live outside session directories so Windows can delete expired sessions."""
        locks = self.root / ".locks"
        locks.mkdir(mode=0o700, exist_ok=True)
        return locks / f"{self.validate(session_id)}.lock"

    # -- state ----------------------------------------------------------------------------------

    @contextmanager
    def lock(self, session_id: str) -> Iterator[None]:
        """Serialise state-changing calls of one session across threads and processes."""
        directory = self.dir(session_id)
        if not directory.is_dir():
            raise SessionNotFound(session_id)
        with FileLock(str(self.lock_path(session_id))):
            if not directory.is_dir():  # swept between the check and the lock
                raise SessionNotFound(session_id)
            yield

    def load(self, session_id: str) -> dict[str, Any]:
        path = self.dir(session_id) / "state.json"
        if not path.is_file():
            raise SessionNotFound(session_id)
        try:
            with path.open("r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Session state is unavailable") from exc
        if not isinstance(state, dict) or state.get("session_id") != session_id:
            raise ValueError("Session state is invalid")
        return state

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        session_id = self.validate(state.get("session_id"))
        directory = self.dir(session_id)
        if not directory.is_dir():
            raise SessionNotFound(session_id)
        state["updated_at"] = utcnow()
        target = directory / "state.json"
        fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2, default=str)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return state

    def create(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        """New session directory + first ``state.json``. Sweeps expired sessions first."""
        self.cleanup()
        state = dict(state or {})
        session_id = str(state.get("session_id") or "")
        if not session_id:
            session_id = self.new_id()
        state["session_id"] = self.validate(session_id)
        state.setdefault("created_at", utcnow())
        self.dir(session_id, create=True)
        return self.save(state)

    def close(self, session_id: str) -> dict[str, Any]:
        """Drop the session directory (after the workflow fetched the result)."""
        if not self.is_valid_id(session_id):
            return {"ok": True, "closed": False, "session_id": str(session_id or "")}
        directory = self.root / session_id
        if not directory.is_dir():
            return {"ok": True, "closed": False, "session_id": session_id}
        try:
            with FileLock(str(self.lock_path(session_id)), timeout=5):
                if directory.is_dir():
                    shutil.rmtree(directory)
            try:
                self.lock_path(session_id).unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": True, "closed": True, "session_id": session_id}
        except Timeout:
            return {"ok": False, "closed": False, "session_id": session_id, "busy": True}

    def cleanup(self) -> int:
        """Best-effort TTL sweep; one scan at a time; busy sessions are left for the next pass."""
        ttl = self.ttl_hours
        if ttl <= 0 or not self._cleanup_guard.acquire(blocking=False):
            return 0
        try:
            threshold = datetime.now(UTC) - timedelta(hours=ttl)
            deleted = 0
            for directory in self.root.iterdir():
                if not directory.is_dir() or not self._id_re.fullmatch(directory.name):
                    continue
                try:
                    with FileLock(str(self.lock_path(directory.name)), timeout=0):
                        with (directory / "state.json").open("r", encoding="utf-8") as fh:
                            created_at = datetime.fromisoformat(json.load(fh)["created_at"])
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=UTC)
                        if created_at < threshold:
                            shutil.rmtree(directory)
                            deleted += 1
                except Timeout:
                    continue
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    continue  # corrupt session must not break new uploads; an operator removes it
            return deleted
        finally:
            self._cleanup_guard.release()
