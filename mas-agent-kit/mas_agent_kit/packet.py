"""``CasePacket`` — everything an agent needs to know about its case, read once.

The orchestrator sends ``agent_task`` with references only (``inputs.artifact_ids``, ``data_refs``);
artifact cards and the results of earlier agents live in the case state served by Activity
(``GET /cases/{id}/state``). The packet binds both:

    packet = CasePacket.load(task, activity)
    packet.artifacts                 # {artifact_id: card} — flat, whatever nesting the state uses
    packet.cards(role="excel")       # cards by role (excel / schedule_source / schedule_include / …)
    packet.upstream_data()           # merged ``data`` of completed agents (later steps win)
    packet.engineer_answers()        # compact HITL view for the LLM
    packet.rework_reason             # orchestrator's remark on the previous result, if any

The flatten algorithm mirrors ``mas-activity-service/app/state_shape.flatten_artifacts`` and the JS twin
in ``n8n/templates/mas_state_utils.py`` (tested against the Activity copy in ``tests/test_packet.py``).
"""

from __future__ import annotations

from typing import Any

from .activity import ActivityClient
from .hitl import engineer_answers, hitl_payloads

INLINE_TEXT_ROLES = frozenset({"schedule_out", "diff"})
NESTED_KEYS = frozenset({"excel", "surface", "schedule", "trajectories", "attachments"})


def role_for_artifact_id(artifact_id: str) -> str:
    key = str(artifact_id or "")
    if key == "excel" or key.startswith("excel_"):
        return "excel"
    if key == "surface":
        return "surface"
    if key == "schedule_source":
        return "schedule_source"
    if key.startswith("schedule_source_"):
        return "schedule_include"
    if key == "schedule_out":
        return "schedule_out"
    if key == "trajectory" or key.startswith("trajectory_"):
        return "trajectory"
    if key == "diff":
        return "diff"
    return "attachment"


def _is_nested(arts: Any) -> bool:
    if not isinstance(arts, dict):
        return False
    sch = arts.get("schedule")
    if isinstance(sch, dict) and any(name in sch for name in ("source", "includes", "grdecl", "out", "diff")):
        return True
    return isinstance(arts.get("trajectories"), list) or isinstance(arts.get("attachments"), list)


def _as_card(artifact_id: str, value: Any) -> dict[str, Any] | None:
    if value is None or value in ("", [], {}):
        return None
    role = role_for_artifact_id(artifact_id)
    if isinstance(value, str):
        if role in INLINE_TEXT_ROLES:
            return {"artifact_id": artifact_id or role, "role": role, "bytes": len(value.encode("utf-8")), "text": value} if value.strip() else None
        return {"artifact_id": artifact_id, "filename": value, "role": role}
    if not isinstance(value, dict):
        return None
    card = dict(value)
    aid = str(card.get("artifact_id") or artifact_id or "").strip()
    if not aid:
        return None
    card["artifact_id"] = aid
    card["role"] = str(card.get("role") or role_for_artifact_id(aid))
    return card


def flatten_artifacts(arts: Any) -> dict[str, dict[str, Any]]:
    """Nested (``schedule.source`` / ``includes`` / ``attachments`` …) or flat state artifacts → ``{id: card}``."""
    src = arts if isinstance(arts, dict) else {}
    out: dict[str, dict[str, Any]] = {}

    def put(value: Any, fallback_id: str = "") -> None:
        card = _as_card(fallback_id, value)
        if card:
            out[str(card["artifact_id"])] = card

    if _is_nested(src):
        put(src.get("excel"), "excel")
        put(src.get("surface"), "surface")
        sch = src.get("schedule") if isinstance(src.get("schedule"), dict) else {}
        put(sch.get("source"), "schedule_source")
        for bag in (sch.get("includes") or [], sch.get("grdecl") or []):
            for item in bag:
                if isinstance(item, dict):
                    put(item, str(item.get("artifact_id") or ""))
        put(sch.get("out"), "schedule_out")
        put(sch.get("diff"), "diff")
        for traj in src.get("trajectories") or []:
            if isinstance(traj, dict):
                put(traj, str(traj.get("artifact_id") or "trajectory"))
        for att in src.get("attachments") or []:
            if isinstance(att, dict):
                put(att, str(att.get("artifact_id") or ""))
        for key, value in src.items():
            if key not in NESTED_KEYS and key not in out:
                put(value, key)
        return out
    for key, value in src.items():
        if key not in {"schedule", "trajectories", "attachments"}:
            put(value, key)
    return out


def upstream_agent_data(state: dict[str, Any]) -> dict[str, Any]:
    """``data`` of every completed agent of the case, merged in step order (later wins).

    Agents do not know each other's names: they take the keys they consume (``facts``, ``new_wells``…)
    from whichever agent produced them. ``state.data.excel`` is honoured for pre-Phase-2 cases.
    ``omitted_keys`` (dropped by the orchestrator for size) is bookkeeping, not data.
    """
    merged: dict[str, Any] = {}
    if not isinstance(state, dict):
        return merged
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    legacy = data.get("excel") if isinstance(data.get("excel"), dict) else {}
    merged.update({k: v for k, v in legacy.items() if k != "omitted_keys"})
    agents = state.get("agents") if isinstance(state.get("agents"), dict) else {}
    slots = [s for s in agents.values() if isinstance(s, dict) and isinstance(s.get("data"), dict)]
    slots.sort(key=lambda s: int(s.get("step") or 0))
    for slot in slots:
        if str(slot.get("status") or "") in ("", "completed"):
            merged.update({k: v for k, v in slot["data"].items() if k != "omitted_keys"})
    return merged


class CasePacket:
    def __init__(self, task: dict[str, Any], state: dict[str, Any] | None = None, activity: ActivityClient | None = None):
        self.task = task if isinstance(task, dict) else {}
        self.state = state if isinstance(state, dict) else {}
        self.activity = activity
        self.inputs: dict[str, Any] = dict(self.task.get("inputs") or {}) if isinstance(self.task.get("inputs"), dict) else {}
        self.context: dict[str, Any] = dict(self.task.get("context") or {}) if isinstance(self.task.get("context"), dict) else {}

    @classmethod
    def load(cls, task: dict[str, Any], activity: ActivityClient | None) -> "CasePacket":
        """Fetch the case state once (tolerant: no Activity → task-only packet)."""
        state = activity.case_state() if activity is not None and activity.configured else {}
        return cls(task, state, activity)

    # -- identity -------------------------------------------------------------------------------

    @property
    def case_id(self) -> str:
        return str(self.task.get("case_id") or "")

    @property
    def task_id(self) -> str:
        return str(self.task.get("task_id") or "")

    @property
    def objective(self) -> str:
        return str(self.task.get("objective") or "")

    @property
    def handoff_message(self) -> str:
        return str(self.task.get("handoff_message") or "")

    @property
    def rework_reason(self) -> str:
        return str(self.inputs.get("rework_reason") or "")

    @property
    def schedule_root(self) -> str:
        return str(self.inputs.get("schedule_root") or self.state.get("schedule_root") or "")

    # -- artifacts ------------------------------------------------------------------------------

    @property
    def artifacts(self) -> dict[str, dict[str, Any]]:
        """Cards from the case state, overridden by cards the orchestrator put into ``inputs.artifacts``."""
        merged = flatten_artifacts(self.state.get("artifacts"))
        merged.update(flatten_artifacts(self.inputs.get("artifacts")))
        return merged

    def cards(self, *, role: str | None = None, suffixes: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        out = []
        for card in self.artifacts.values():
            if role and str(card.get("role") or "") != role:
                continue
            if suffixes and not str(card.get("filename") or "").lower().endswith(suffixes):
                continue
            out.append(card)
        return out

    def download(self, artifact_id: str) -> tuple[bytes, str]:
        if self.activity is None or not self.activity.configured:
            raise FileNotFoundError(f"activity is not configured; cannot download {artifact_id}")
        return self.activity.download(artifact_id)

    # -- facts from other actors ----------------------------------------------------------------

    def upstream_data(self) -> dict[str, Any]:
        return upstream_agent_data(self.state)

    def engineer_answers(self) -> list[dict[str, Any]]:
        return engineer_answers(self.context)

    def hitl_payloads(self) -> list[Any]:
        return hitl_payloads(self.context)
