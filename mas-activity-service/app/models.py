"""HTTP request/response models for MAS Activity."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


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
