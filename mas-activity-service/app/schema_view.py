"""Build scrubbable schema frames from control-plane events.

The Activity UI mirrors this mapping in static/schema.js. Keep both aligned:
node caption stays inside the box; handoff_message rides the edge slip.
"""

from __future__ import annotations

import json
from typing import Any

NODE_KEYS = ("input", "orchestrator", "excel", "calc", "schedule", "user", "output")
EDGE_KEYS = (
    "in_orch",
    "orch_out",
    "orch_excel",
    "orch_calc",
    "orch_schedule",
    "excel_orch",
    "calc_orch",
    "schedule_orch",
    "orch_user",
    "user_orch",
)
AGENT_NODES = {
    "excel_extractor": "excel",
    "calculation_agent": "calc",
    "schedule_builder": "schedule",
}
OUTBOUND_EDGE = {
    "excel": "orch_excel",
    "calc": "orch_calc",
    "schedule": "orch_schedule",
}
RETURN_EDGE = {
    "excel": "excel_orch",
    "calc": "calc_orch",
    "schedule": "schedule_orch",
}
START_LABEL = "Постановка задачи"
END_LABEL = "Результат"
KIND_LABELS = {
    "case.created": START_LABEL,
    "case.finished": END_LABEL,
    "case.failed": END_LABEL,
    "orchestrator.status": "Оркестратор",
    "orchestrator.decision": "Оркестратор",
    "agent.handoff": "Передача",
    "agent.accepted": "Агент принял задачу",
    "agent.progress": "Агент работает",
    "agent.result": "Агент вернул результат",
    "agent.failed": "Сбой агента",
    "hitl.request": "Запрос к вам",
    "hitl.answered": "Ваш ответ",
    "system.node_error": "Сбой узла",
}


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _files_from_state(state: dict[str, Any]) -> list[str]:
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    names: list[str] = []
    seen: set[str] = set()
    skip = {"schedule_out", "diff"}
    for key, item in artifacts.items():
        if key in skip:
            continue
        name = ""
        if isinstance(item, dict):
            name = _text(item.get("filename") or item.get("artifact_id") or key)
        elif item not in (None, "", {}, []):
            name = _text(key)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _result_text(payload: Any, status_message: str) -> str:
    data = payload if isinstance(payload, dict) else {}
    result = data.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        for key in ("summary", "message", "text", "status_message"):
            if _text(result.get(key)):
                return _text(result.get(key))
        return json.dumps(result, ensure_ascii=False)[:400]
    for key in ("message", "summary", "text"):
        if _text(data.get(key)):
            return _text(data.get(key))
    return status_message


def _blank_graph(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": {key: {"tone": "idle", "bubble": None, "caption": ""} for key in NODE_KEYS},
        "edges": {key: {"tone": "idle", "bubble": None} for key in EDGE_KEYS},
        "input": {"goal": _text(state.get("goal")), "files": _files_from_state(state)},
        "output": {"result": "", "prompt": ""},
        "active_node": None,
        "active_edge": None,
        "in_flight": None,
        "last_handoff": {},
        "last_orch_prompt": "",
    }


def _set_caption(graph: dict[str, Any], node_id: str, text: str | None) -> None:
    caption = _text(text)
    if caption:
        graph["nodes"][node_id]["caption"] = caption


def _clear_node_bubbles(graph: dict[str, Any], keep: str | None = None) -> None:
    for node_id, node in graph["nodes"].items():
        if node_id != keep:
            node["bubble"] = None


def _activate_node(graph: dict[str, Any], node_id: str, bubble: str | None) -> None:
    for nid, node in graph["nodes"].items():
        if nid == node_id:
            continue
        if node["tone"] == "active":
            node["tone"] = "done"
        node["bubble"] = None
    node = graph["nodes"][node_id]
    node["tone"] = "active"
    node["bubble"] = bubble or None
    _set_caption(graph, node_id, bubble)
    graph["active_node"] = node_id


def _mark_done(graph: dict[str, Any], node_id: str) -> None:
    node = graph["nodes"][node_id]
    if node["tone"] != "error":
        node["tone"] = "done"
    node["bubble"] = None
    if graph.get("active_node") == node_id:
        graph["active_node"] = None


def _set_edge(graph: dict[str, Any], edge_id: str, tone: str, bubble: str | None = None) -> None:
    for eid, edge in graph["edges"].items():
        if eid == edge_id:
            continue
        if edge["tone"] == "active" and tone == "active":
            edge["tone"] = "done"
            edge["bubble"] = None
    edge = graph["edges"][edge_id]
    edge["tone"] = tone
    edge["bubble"] = bubble if tone == "active" else None
    graph["active_edge"] = edge_id if tone == "active" else (graph.get("active_edge") if graph.get("active_edge") != edge_id else None)


def _agent_node(event: dict[str, Any]) -> str | None:
    agent_id = _text(event.get("agent_id"))
    if agent_id in AGENT_NODES:
        return AGENT_NODES[agent_id]
    actor = _text(event.get("actor"))
    return AGENT_NODES.get(actor)


def _frame_label(event: dict[str, Any]) -> str:
    kind = _text(event.get("kind"))
    if kind in { "case.created" }:
        return START_LABEL
    if kind in { "case.finished", "case.failed" }:
        return END_LABEL
    handoff = _text(event.get("handoff_message"))
    if kind == "agent.handoff" and handoff:
        return handoff
    message = _text(event.get("status_message"))
    if message:
        return message
    return KIND_LABELS.get(kind) or kind or "Шаг"


def _snapshot(graph: dict[str, Any], event: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "label": _frame_label(event),
        "kind": _text(event.get("kind")),
        "event_id": event.get("event_id"),
        "nodes": _copy(graph["nodes"]),
        "edges": _copy(graph["edges"]),
        "input": _copy(graph["input"]),
        "output": _copy(graph["output"]),
        "active_node": graph.get("active_node"),
        "active_edge": graph.get("active_edge"),
    }


def _apply_event(graph: dict[str, Any], event: dict[str, Any]) -> None:
    kind = _text(event.get("kind"))
    status_message = _text(event.get("status_message"))
    handoff = _text(event.get("handoff_message"))
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    agent_node = _agent_node(event)

    if kind == "case.created":
        files = payload.get("files")
        if isinstance(files, list) and files:
            graph["input"]["files"] = [str(name) for name in files if str(name).strip()]
        if not graph["input"]["goal"] and status_message:
            graph["input"]["goal"] = status_message
        _activate_node(graph, "input", None)
        _set_edge(graph, "in_orch", "active")
        return

    if kind in { "orchestrator.status", "orchestrator.decision" }:
        if graph["nodes"]["input"]["tone"] == "active":
            _mark_done(graph, "input")
        if graph["edges"]["in_orch"]["tone"] == "active":
            _set_edge(graph, "in_orch", "done")
        in_flight = graph.get("in_flight")
        if in_flight:
            ret = RETURN_EDGE.get(in_flight)
            if ret and graph["edges"][ret]["tone"] == "active":
                _set_edge(graph, ret, "done")
        graph["last_orch_prompt"] = status_message or graph.get("last_orch_prompt") or ""
        _activate_node(graph, "orchestrator", status_message or None)
        return

    if kind == "agent.handoff" and agent_node:
        graph["in_flight"] = agent_node
        graph["last_handoff"][agent_node] = handoff
        graph["nodes"][agent_node]["tone"] = "pending"
        _set_edge(graph, OUTBOUND_EDGE[agent_node], "active", handoff or None)
        if graph["nodes"]["orchestrator"]["tone"] != "error":
            graph["nodes"]["orchestrator"]["tone"] = "active"
            if status_message:
                graph["nodes"]["orchestrator"]["bubble"] = status_message
                _set_caption(graph, "orchestrator", status_message)
                graph["active_node"] = "orchestrator"
                _clear_node_bubbles(graph, keep="orchestrator")
        return

    if kind in { "agent.accepted", "agent.progress" } and agent_node:
        graph["in_flight"] = agent_node
        outbound = OUTBOUND_EDGE[agent_node]
        kept = handoff or graph["last_handoff"].get(agent_node) or graph["edges"][outbound].get("bubble")
        _activate_node(graph, agent_node, status_message or None)
        _set_edge(graph, outbound, "active", kept)
        return

    if kind == "agent.result" and agent_node:
        _set_caption(graph, agent_node, status_message)
        _mark_done(graph, agent_node)
        _set_edge(graph, OUTBOUND_EDGE[agent_node], "done")
        _set_edge(graph, RETURN_EDGE[agent_node], "active")
        graph["in_flight"] = None
        graph["nodes"]["orchestrator"]["tone"] = "pending"
        graph["nodes"]["orchestrator"]["bubble"] = None
        graph["active_node"] = None
        return

    if kind == "agent.failed" and agent_node:
        graph["nodes"][agent_node]["tone"] = "error"
        graph["nodes"][agent_node]["bubble"] = status_message or None
        _set_caption(graph, agent_node, status_message)
        graph["active_node"] = agent_node
        _clear_node_bubbles(graph, keep=agent_node)
        _set_edge(graph, OUTBOUND_EDGE[agent_node], "done")
        _set_edge(graph, RETURN_EDGE[agent_node], "error")
        graph["in_flight"] = None
        return

    if kind == "hitl.request":
        question = status_message or _text(payload.get("question"))
        _activate_node(graph, "user", question or None)
        graph["nodes"]["orchestrator"]["tone"] = "waiting"
        graph["nodes"]["orchestrator"]["bubble"] = None
        _set_edge(graph, "orch_user", "active", question or None)
        return

    if kind == "hitl.answered":
        _mark_done(graph, "user")
        _set_edge(graph, "orch_user", "done")
        _set_edge(graph, "user_orch", "active")
        _activate_node(graph, "orchestrator", status_message or None)
        return

    if kind == "case.finished":
        for node_id, node in graph["nodes"].items():
            if node_id == "output":
                continue
            if node["tone"] in { "active", "pending", "waiting" }:
                node["tone"] = "done"
            node["bubble"] = None
        _set_caption(graph, "orchestrator", status_message or graph.get("last_orch_prompt"))
        for edge in graph["edges"].values():
            if edge["tone"] == "active":
                edge["tone"] = "done"
            edge["bubble"] = None
        graph["output"]["prompt"] = status_message or graph.get("last_orch_prompt") or ""
        graph["output"]["result"] = _result_text(payload, status_message)
        _activate_node(graph, "output", None)
        _set_edge(graph, "orch_out", "active")
        graph["in_flight"] = None
        graph["active_node"] = "output"
        return

    if kind == "case.failed":
        graph["output"]["prompt"] = status_message or graph.get("last_orch_prompt") or ""
        graph["output"]["result"] = status_message or "Задача завершилась с ошибкой."
        graph["nodes"]["orchestrator"]["tone"] = "error"
        graph["nodes"]["orchestrator"]["bubble"] = status_message or None
        _set_caption(graph, "orchestrator", status_message)
        graph["nodes"]["output"]["tone"] = "error"
        graph["nodes"]["output"]["bubble"] = None
        _clear_node_bubbles(graph, keep="orchestrator")
        _set_edge(graph, "orch_out", "error")
        graph["active_node"] = "orchestrator"
        graph["in_flight"] = None
        return

    if kind == "system.node_error":
        graph["nodes"]["orchestrator"]["tone"] = "error"
        graph["nodes"]["orchestrator"]["bubble"] = status_message or None
        _set_caption(graph, "orchestrator", status_message)
        graph["active_node"] = "orchestrator"
        _clear_node_bubbles(graph, keep="orchestrator")
        return


def build_schema_frames(events: list[dict[str, Any]] | None, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    state = state if isinstance(state, dict) else {}
    graph = _blank_graph(state)
    rows = [event for event in (events or []) if isinstance(event, dict)]
    if not rows:
        _activate_node(graph, "input", None)
        return [
            {
                "index": 0,
                "label": START_LABEL,
                "kind": "case.created",
                "event_id": None,
                "nodes": _copy(graph["nodes"]),
                "edges": _copy(graph["edges"]),
                "input": _copy(graph["input"]),
                "output": _copy(graph["output"]),
                "active_node": "input",
                "active_edge": None,
            }
        ]
    frames: list[dict[str, Any]] = []
    for event in rows:
        _apply_event(graph, event)
        frames.append(_snapshot(graph, event, len(frames)))
    return frames


def build_schema_model(
    events: list[dict[str, Any]] | None,
    state: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    frames = build_schema_frames(events, state=state)
    last_kind = frames[-1]["kind"] if frames else ""
    complete = _text(status) in { "done", "failed" } or last_kind in { "case.finished", "case.failed" }
    return {
        "frames": frames,
        "complete": complete,
        "start_label": START_LABEL,
        "end_label": END_LABEL,
    }
