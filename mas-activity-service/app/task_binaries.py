"""Persist start multipart binaries so HITL resume can re-attach them to n8n.

Orchestrator HITL actions are JSON-only; Excel/schedule specialists still need the
original workbook and INCLUDE stubs on every resume that re-delegates.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from app.persist import state_path
from app.settings import get_settings

BinaryMap = dict[str, tuple[str, bytes, str]]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_TASKS_ON_DISK = 80


def binaries_root() -> Path | None:
    raw = get_settings().activity_binaries_path.strip()
    if raw:
        return Path(raw).expanduser()
    sp = state_path()
    if sp is None:
        return None
    return sp.parent / "task_binaries"


def _task_dir(task_id: str) -> Path | None:
    root = binaries_root()
    if root is None:
        return None
    safe = _SAFE.sub("_", task_id)[:180] or "task"
    return root / safe


def save_task_binaries(task_id: str, files: BinaryMap | None) -> None:
    if not files:
        return
    dest = _task_dir(task_id)
    if dest is None:
        return
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for i, (key, (filename, content, mime)) in enumerate(files.items()):
        safe_name = _SAFE.sub("_", Path(filename).name)[:160] or f"blob_{i}"
        rel = f"{_SAFE.sub('_', key)[:80]}__{i}__{safe_name}"
        (dest / rel).write_bytes(content)
        manifest.append(
            {
                "key": key,
                "filename": filename,
                "mime_type": mime or "application/octet-stream",
                "path": rel,
            }
        )
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _prune_old(dest.parent)


def load_task_binaries(task_id: str) -> BinaryMap:
    dest = _task_dir(task_id)
    if dest is None or not dest.is_dir():
        return {}
    man_path = dest / "manifest.json"
    if not man_path.is_file():
        return {}
    try:
        raw = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, list):
        return {}
    out: BinaryMap = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        rel = str(row.get("path") or "").strip()
        filename = str(row.get("filename") or rel).strip()
        mime = str(row.get("mime_type") or "application/octet-stream")
        if not key or not rel or ".." in rel or "/" in rel or "\\" in rel:
            continue
        blob = dest / rel
        if not blob.is_file():
            continue
        out[key] = (filename, blob.read_bytes(), mime)
    return out


def clear_task_binaries(task_id: str) -> None:
    dest = _task_dir(task_id)
    if dest is None or not dest.exists():
        return
    shutil.rmtree(dest, ignore_errors=True)


def _prune_old(root: Path) -> None:
    if not root.is_dir():
        return
    kids = [p for p in root.iterdir() if p.is_dir()]
    if len(kids) <= _MAX_TASKS_ON_DISK:
        return
    kids.sort(key=lambda p: p.stat().st_mtime)
    for old in kids[: max(0, len(kids) - _MAX_TASKS_ON_DISK)]:
        shutil.rmtree(old, ignore_errors=True)
