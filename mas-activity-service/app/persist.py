"""Optional on-disk Activity store so container recreate does not wipe the rail."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.settings import get_settings


from app.settings import get_settings


CONTRACT = "mas_activity_state"
CONTRACT_VERSION = "1.0"


def state_path() -> Path | None:
    raw = get_settings().activity_state_path.strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def persist_enabled() -> bool:
    return state_path() is not None


def load_state() -> tuple[dict[str, dict[str, Any]], list[str]]:
    path = state_path()
    if path is None or not path.is_file():
        return {}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, []
    if not isinstance(raw, dict):
        return {}, []
    tasks_raw = raw.get("tasks")
    order_raw = raw.get("order")
    if not isinstance(tasks_raw, dict) or not isinstance(order_raw, list):
        return {}, []
    tasks: dict[str, dict[str, Any]] = {}
    for task_id, task in tasks_raw.items():
        if not isinstance(task_id, str) or not isinstance(task, dict):
            continue
        tasks[task_id] = dict(task)
        tasks[task_id]["task_id"] = task_id
        if not isinstance(tasks[task_id].get("turns"), list):
            tasks[task_id]["turns"] = []
        if "transcript_loaded" not in tasks[task_id]:
            tasks[task_id]["transcript_loaded"] = bool(tasks[task_id]["turns"])
    order = [str(x) for x in order_raw if isinstance(x, str) and x in tasks]
    for task_id in tasks:
        if task_id not in order:
            order.append(task_id)
    return tasks, order


def save_state(tasks: dict[str, dict[str, Any]], order: list[str]) -> None:
    path = state_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract": CONTRACT,
        "contract_version": CONTRACT_VERSION,
        "order": list(order),
        "tasks": {tid: tasks[tid] for tid in order if tid in tasks},
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".activity-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
