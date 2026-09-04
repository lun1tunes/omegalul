"""Decision / AgentTask / AgentResult contracts for the thin orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.state_shape import compact_decision_context as compact_decision_context

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

CASE_STATUSES = ("new", "running", "waiting_user", "done", "failed")
AGENT_RESULT_STATUSES = ("completed", "needs_input", "failed")
DECISION_ACTIONS = ("call_agent", "ask_user", "finish")
EVENT_KINDS = (
    "case.created",
    "case.finished",
    "case.failed",
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
)
AGENT_EVENT_KINDS = ("agent.accepted", "agent.progress", "agent.result", "agent.failed")
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
    status: Literal["new", "running", "waiting_user", "done", "failed"] = "new"
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
    status: Literal["completed", "needs_input", "failed"]
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    assumptions: list[Any] = Field(default_factory=list)
    requests: list[dict[str, Any]] = Field(default_factory=list)


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


class CaseNameIn(BaseModel):
    task_name: str = ""


def empty_state(case_id: str, goal: str = "") -> dict[str, Any]:
    return CaseState(case_id=case_id, goal=goal, status="new", version=1).model_dump()


def load_json_schema(name: str) -> dict[str, Any]:
    path = SCHEMAS / name
    return json.loads(path.read_text(encoding="utf-8"))
