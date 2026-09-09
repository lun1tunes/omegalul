"""Decision / AgentTask / AgentResult contracts for the thin orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.state_shape import compact_decision_context as compact_decision_context

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

# ``waiting_agent``: a long agent returned ``in_progress``; the case waits for ``resume source=agent``.
CASE_STATUSES = ("new", "running", "waiting_user", "waiting_agent", "done", "failed", "cancelled")
AGENT_RESULT_STATUSES = ("completed", "needs_input", "failed", "in_progress")
DECISION_ACTIONS = ("call_agent", "ask_user", "finish")
EVENT_KINDS = (
    "case.created",
    "case.finished",
    "case.failed",
    "case.cancelled",
    "orchestrator.status",
    "orchestrator.decision",
    "agent.handoff",
    "agent.accepted",
    "agent.progress",
    "agent.result",
    "agent.failed",
    "hitl.request",
    "hitl.answered",
    "system.node_error",
    # Developer trace (hidden from the chat, shown in the «Лог» tab): one FastAPI tool call of an
    # agent (`trace.tool`) or a free-form technical note (`trace.note`). Same table, same stream.
    "trace.tool",
    "trace.note",
)
AGENT_EVENT_KINDS = ("agent.accepted", "agent.progress", "agent.result", "agent.failed")
TRACE_EVENT_PREFIX = "trace."


def is_trace_kind(kind: Any) -> bool:
    """Technical events never become chat turns; the developer log shows them."""
    return str(kind or "").startswith(TRACE_EVENT_PREFIX)
MAX_STEPS = 24


class PlanItem(BaseModel):
    id: str
    title: str | None = None
    status: str = "pending"


class HitlState(BaseModel):
    pending: bool = False
    questions: list[dict[str, Any]] = Field(default_factory=list)
    answers: dict[str, Any] = Field(default_factory=dict)


class CaseState(BaseModel):
    case_id: str
    goal: str = ""
    task_name: str = ""
    status: Literal["new", "running", "waiting_user", "waiting_agent", "done", "failed", "cancelled"] = "new"
    plan: list[PlanItem] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    current_task: dict[str, Any] | None = None
    hitl: HitlState = Field(default_factory=HitlState)
    last_error: dict[str, Any] | None = None
    step_count: int = 0
    version: int = 1


class CallAgentAction(BaseModel):
    type: Literal["call_agent"] = "call_agent"
    agent_id: str
    task_id: str
    handoff_message: str = ""
    task: dict[str, Any] = Field(default_factory=dict)


class AskUserAction(BaseModel):
    type: Literal["ask_user"] = "ask_user"
    question_id: str
    question: str
    options: list[str] = Field(default_factory=list)


class FinishAction(BaseModel):
    type: Literal["finish"] = "finish"
    result: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    status_message: str
    plan_update: list[PlanItem] = Field(default_factory=list)
    action: CallAgentAction | AskUserAction | FinishAction

    @model_validator(mode="before")
    @classmethod
    def _coerce_action(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        action = value.get("action")
        if not isinstance(action, dict):
            return value
        kind = str(action.get("type") or "").strip()
        if kind == "call_agent":
            value = {**value, "action": CallAgentAction.model_validate(action)}
        elif kind == "ask_user":
            value = {**value, "action": AskUserAction.model_validate(action)}
        elif kind == "finish":
            value = {**value, "action": FinishAction.model_validate(action)}
        return value


class AgentTask(BaseModel):
    case_id: str
    task_id: str
    agent_id: str
    objective: str = ""
    handoff_message: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentIssue(BaseModel):
    type: str
    detail: str | None = None
    well: str | None = None
    source_row: int | None = None


class AgentRequest(BaseModel):
    question_id: str
    question: str
    options: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    task_id: str
    status: Literal["completed", "needs_input", "failed", "in_progress"]
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[Any] = Field(default_factory=list)
    requests: list[dict[str, Any]] = Field(default_factory=list)
    #: ``in_progress`` only: what the agent is waiting on (``{"kind": "poll", "ref": "job-1", "poll_hint": "20s"}``).
    watch: dict[str, Any] = Field(default_factory=dict)


class CaseEventIn(BaseModel):
    kind: str
    actor: str
    agent_id: str | None = None
    status: str | None = None
    status_message: str | None = None
    handoff_message: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        kind = value.strip()
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        return kind


class CaseAnswerIn(BaseModel):
    question_id: str
    answer: Any
    requested_by: str = "mas activity user"
    expected_version: int | None = None
    # Value of a selected question option (Activity renders `options[]` as buttons).
    choice: str | None = None


class CaseNameIn(BaseModel):
    task_name: str = ""


class AgentRegistryIn(BaseModel):
    """``PUT /agents/{agent_id}`` body — a partial ``agent_registry`` row (Phase 2, executable registry).

    Every field is optional: the row is merged over the existing one, so the field engineer can bind a
    UI-imported workflow with just ``{"invoke": {"kind": "n8n_workflow", "workflow_id": "…"}}`` or disable
    an agent with ``{"enabled": false}``. Column set mirrors ``n8n/templates/mas_agent_registry.py``.
    """

    title: str | None = None
    when_to_use: str | None = None
    input_required: list[str] | None = None
    output_provides: list[str] | None = None
    invoke: dict[str, Any] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    hitl_policy: Literal["agent_asks", "never"] | None = None
    enabled: bool | None = None
    version: str | None = None

    @field_validator("invoke")
    @classmethod
    def _invoke_shape(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None or not value:
            return value
        kind = str(value.get("kind") or "")
        if kind == "n8n_workflow":
            if not str(value.get("workflow_id") or "").strip():
                raise ValueError("invoke.kind=n8n_workflow requires invoke.workflow_id")
        elif kind == "http":
            if not str(value.get("url") or "").strip():
                raise ValueError("invoke.kind=http requires invoke.url")
        else:
            raise ValueError("invoke.kind must be n8n_workflow or http")
        return value


def empty_state(case_id: str, goal: str = "") -> dict[str, Any]:
    return CaseState(case_id=case_id, goal=goal, status="new", version=1).model_dump()


def load_json_schema(name: str) -> dict[str, Any]:
    path = SCHEMAS / name
    return json.loads(path.read_text(encoding="utf-8"))
