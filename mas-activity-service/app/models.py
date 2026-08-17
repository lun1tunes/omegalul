"""HTTP request/response models for MAS Activity."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class TurnIn(BaseModel):
    at: str | None = None
    stage: str | None = None
    status: str | None = None
    summary: str | None = None
    brief: str | None = None
    duration_ms: int | None = None
    from_specialist: str | None = None
    to_specialist: str | None = None
    from_role: str | None = None
    to_role: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    event_type: str = "handoff"


class TurnPost(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    trace_id: str | None = None
    turn: TurnIn

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_RE.match(value):
            raise ValueError("task_id has invalid characters")
        return value


class EventIn(BaseModel):
    event_type: str | None = None
    stage: str | None = None
    status: str | None = None
    summary: str | None = None
    brief: str | None = None
    duration_ms: int | None = None
    actor: str | None = None
    at: str | None = None
    handoff: dict[str, Any] | None = None
    task_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    human_gate: dict[str, Any] | None = None


class SyncPost(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)
    trace_id: str | None = None
    status: str | None = None
    version: int | None = None
    human_gate: dict[str, Any] | None = None
    events: list[EventIn] = Field(default_factory=list)
    turns: list[TurnIn] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _task_id(cls, value: str) -> str:
        if not TASK_ID_RE.match(value):
            raise ValueError("task_id has invalid characters")
        return value


class HitlPost(BaseModel):
    action: Literal["reply", "approve", "reject", "cancel", "status", "retry"] = "reply"
    human_response: str | None = None
    requested_by: str = Field(min_length=1, max_length=120)
    gate_id: str | None = None
    expected_version: int | None = None


class KnowledgeDocumentPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    title: str | None = Field(default=None, min_length=1)
    keywords: list[str] | None = None
    topics: list[str] | None = None
    task_patterns: list[str] | None = None


class KnowledgeDocumentCreate(BaseModel):
    target_base: str = Field(min_length=1, max_length=120)
    knowledge_id: str = Field(min_length=2, max_length=119)
    knowledge_type: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    task_patterns: list[str] = Field(default_factory=list)
    author: str | None = Field(default=None, max_length=120)


class TaskMeta(BaseModel):
    task_id: str
    title: str | None = None
    updated_at: str
    turn_count: int | None = None
    last_status: str | None = None
    last_at_abs: str | None = None
    status: str | None = None
    awaiting_human: bool = False
