"""Optional on-disk root used as the parent of ``task_binaries`` when ACTIVITY_BINARIES_PATH is unset."""

from __future__ import annotations

from pathlib import Path

from app.settings import get_settings


def state_path() -> Path | None:
    raw = get_settings().activity_state_path.strip()
    if not raw:
        return None
    return Path(raw).expanduser()
