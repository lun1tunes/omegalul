"""One control-plane poller per open case SSE stream.

n8n records an execution per webhook. The Activity UI used to call
``snapshot`` every 2s per browser tab. This hub:

- shares one poller across tabs for the same case
- keeps 2s while the case is ``running`` / has new events
- backs off to 6s when idle (HITL / done / failed)
- wakes immediately on writes (create / update / append) so HITL answers
  and agent events still land without waiting for the idle interval
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import control_plane

RUNNING_INTERVAL_S = 2.0
IDLE_INTERVAL_S = 6.0
IDLE_STATUSES = frozenset({"waiting_user", "done", "failed"})
# One coalesced slot per subscriber: overflow merges events instead of dropping them.
QUEUE_MAX = 1

_subs: dict[str, list[asyncio.Queue]] = {}
_tasks: dict[str, asyncio.Task] = {}
_wakes: dict[str, asyncio.Event] = {}
_lock = asyncio.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def poll_interval(status: str | None, had_events: bool) -> float:
    if had_events:
        return RUNNING_INTERVAL_S
    if str(status or "") in IDLE_STATUSES:
        return IDLE_INTERVAL_S
    return RUNNING_INTERVAL_S


def notify_case(case_id: str) -> None:
    """Wake the SSE poller from a sync write path (thread-safe)."""
    cid = str(case_id or "").strip()
    loop = _loop
    if not cid or loop is None:
        return

    def _set() -> None:
        ev = _wakes.get(cid)
        if ev is not None:
            ev.set()

    try:
        loop.call_soon_threadsafe(_set)
    except RuntimeError:
        return


async def subscribe(case_id: str) -> asyncio.Queue:
    global _loop
    cid = str(case_id or "").strip()
    if not cid:
        raise ValueError("case_id is required")
    _loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    async with _lock:
        _subs.setdefault(cid, []).append(queue)
        _wakes.setdefault(cid, asyncio.Event())
        if cid not in _tasks or _tasks[cid].done():
            _tasks[cid] = asyncio.create_task(_run(cid), name=f"case-watch:{cid}")
    return queue


async def unsubscribe(case_id: str, queue: asyncio.Queue) -> None:
    cid = str(case_id or "").strip()
    async with _lock:
        holders = _subs.get(cid) or []
        if queue in holders:
            holders.remove(queue)
        if holders:
            return
        _subs.pop(cid, None)
        wake = _wakes.pop(cid, None)
        if wake is not None:
            wake.set()
        task = _tasks.pop(cid, None)
        if task is not None and not task.done():
            task.cancel()


def merge_watch_payload(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Keep the latest case row and every event id from both payloads."""
    incoming = new if isinstance(new, dict) else {}
    if not isinstance(old, dict):
        return {
            "case": incoming.get("case"),
            "events": list(incoming.get("events") or []),
        }
    events: list[Any] = []
    seen: set[int] = set()
    for item in list(old.get("events") or []) + list(incoming.get("events") or []):
        if not isinstance(item, dict):
            events.append(item)
            continue
        eid = item.get("event_id")
        if eid is None:
            events.append(item)
            continue
        try:
            key = int(eid)
        except (TypeError, ValueError):
            events.append(item)
            continue
        if key in seen:
            continue
        seen.add(key)
        events.append(item)
    case = incoming.get("case")
    if case is None:
        case = old.get("case")
    return {"case": case, "events": events}


async def _publish(case_id: str, payload: dict[str, Any]) -> None:
    async with _lock:
        holders = list(_subs.get(case_id) or [])
    for queue in holders:
        pending = None
        if queue.full():
            try:
                pending = queue.get_nowait()
            except asyncio.QueueEmpty:
                pending = None
        item = merge_watch_payload(pending, payload)
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                extra = queue.get_nowait()
            except asyncio.QueueEmpty:
                extra = None
            merged = merge_watch_payload(extra, item)
            try:
                queue.put_nowait(merged)
            except asyncio.QueueFull:
                continue


async def _run(case_id: str) -> None:
    last = 0
    interval = RUNNING_INTERVAL_S
    try:
        while True:
            async with _lock:
                if not _subs.get(case_id):
                    return
                wake = _wakes.setdefault(case_id, asyncio.Event())
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            wake.clear()
            fresh = await asyncio.to_thread(control_plane.snapshot, case_id, last)
            row = fresh.get("case") if isinstance(fresh, dict) else None
            events = list((fresh or {}).get("events") or [])
            if events:
                last = max(
                    last,
                    max(int(event["event_id"]) for event in events if event.get("event_id") is not None),
                )
            status = str((row or {}).get("status") or "")
            interval = poll_interval(status, bool(events))
            await _publish(case_id, {"case": row, "events": events})
    except asyncio.CancelledError:
        return


def reset() -> None:
    """Tests: drop pollers. Best-effort; running tasks are cancelled."""
    global _loop
    _loop = None
    for task in list(_tasks.values()):
        task.cancel()
    _tasks.clear()
    _subs.clear()
    _wakes.clear()
