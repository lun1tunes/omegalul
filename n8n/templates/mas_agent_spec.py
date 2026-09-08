"""``AgentSpec`` — one declaration per agent; everything else is generated from it.

A spec drives three artefacts (MAS_REFACTORING_PLAN.md, Phase 4.2):

* the n8n workflow ``Agent — <title>`` (``mas_agent_workflow.write_workflow(spec)``);
* the ``agent_registry`` row the orchestrator plans with (``spec.registry_row()`` → ``mas_agent_registry.SEED``);
* the ``MAS — Runtime Config`` field with the service URL (``spec.service_url_key`` / ``spec.lab_url``).

Specs live in ``n8n/templates/agents/<agent_id>.py`` (``SPEC = AgentSpec(...)``) and are listed in
``agents/__init__.py``. HTTP-only agents (no n8n workflow, e.g. Calculation Agent) fill the registry
fields and ``invoke`` and leave ``system_prompt`` empty.

The agent workflow generated from a spec always has the same shape:

    trigger → Runtime configuration → Normalize task → open_session → [Activity accepted/progress]
    → Prepare AI Agent input → Knowledge Retrieval → AI Agent (+ tools as HTTP Request) → Summarize
    → Fetch /sessions/{id}/result → Format → Close session

so an engineer who read one agent workflow can read every other one.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

# One LLM tool exposed as an HTTP Request node: (arg name, $fromAI type, required, description for the LLM).
ToolField = tuple[str, str, bool, str]
# (tool name = FastAPI route /agent-tools/<name>, description for the LLM, fields).
ToolDef = tuple[str, str, list[ToolField]]


# n8n Header Auth credential the Excel Tools service expects (``X-API-Key``); bound in the UI after import.
EXCEL_KEY_CRED = {"httpHeaderAuth": {"id": "REPLACE_IN_UI", "name": "REPLACE: Excel Tools X-API-Key"}}


def workflow_id_for(agent_id: str) -> str:
    """Stable n8n workflow id derived from the agent id (lab CLI import keeps it; UI import re-mints it)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-agent:{agent_id}"))


@dataclass
class FallbackTexts:
    """Russian prose shown to the engineer when the LLM ended without fixing a result (no tool names)."""

    #: LLM finished without a stored result.
    no_result_question: str
    #: Same, but some tool was called more than three times.
    repeated_question: str
    #: Message when a result exists but the LLM said nothing.
    done_message: str
    #: ``issues[].type`` for "no result" (e.g. ``no_extract`` / ``no_apply``).
    no_result_issue: str
    #: ``question_id`` of the fallback HITL request (``Q-clarify`` / ``Q-apply``).
    question_id: str
    #: File kinds the engineer may attach with the answer.
    accepts_files: list[str] = field(default_factory=lambda: ["xlsx"])
    #: ``open_session`` returned ``ok:false`` without a result (no input file).
    missing_input_message: str = "Нет входных данных для агента"
    missing_input_issue: str = "missing_input"
    missing_input_question: str = "Приложите файлы, нужные для этой задачи."
    #: ``GET /sessions/{id}/result`` returned nothing usable.
    no_result_failed_message: str = "Агент не вернул результат"
    no_result_failed_issue: str = "agent_no_result"


@dataclass
class AgentSpec:
    # -- identity (registry + workflow) ----------------------------------------------------------
    agent_id: str
    title: str
    #: What the orchestrator's Decision LLM reads to decide whether to call this agent. Russian prose.
    when_to_use: str
    #: Artifact roles / data keys the agent needs and produces (planning facts for the orchestrator).
    input_required: list[str]
    output_provides: list[str]
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    #: ``agent_asks`` — may raise ``needs_input`` questions; ``never`` — pure computation.
    hitl_policy: str = "agent_asks"
    enabled: bool = True
    version: str = "2"

    # -- how the orchestrator reaches the agent ---------------------------------------------------
    #: Runtime Config field holding the service URL (``excel_tools_url``); empty for HTTP-only agents without a URL.
    service_url_key: str = ""
    #: Lab Compose address seeded into Runtime Config; the field engineer overwrites it in the UI.
    lab_url: str = ""
    #: Service label for health checks / docs when it differs from the agent title (Excel Extractor runs on "Excel Tools").
    service_title: str = ""
    #: Explicit ``invoke`` for agents without an n8n workflow (``{"kind": "http", "url": "{math_url}/agent/run"}``).
    invoke_override: dict[str, Any] | None = None

    # -- the n8n workflow (empty system_prompt = HTTP-only agent, no workflow generated) ------------
    #: Short lowercase word used in node names: ``Open <slug> session``, ``Fetch <slug> result``.
    slug: str = ""
    system_prompt: str = ""
    tools: list[ToolDef] = field(default_factory=list)
    #: Key of ``mas_retrieval_client.SELECTORS`` (``excel`` / ``schedule``) — which RAG slice the LLM gets.
    rag_selector: str = ""
    rag_ready_note: str = ""
    rag_empty_note: str = ""
    #: JS appended to Prepare: may fill ``keyword_families`` / ``topics`` / ``task_patterns`` arrays
    #: from ``blob`` / ``low`` (task text) for the retrieval filters. Optional.
    retrieval_filters_js: str = ""
    #: JS expression (object literal body) with extra ``planner_input`` fields taken from ``opened``.
    planner_extra_js: str = ""
    #: Tool-name prefixes / names whose call fixes a result in the session (``extract_``, ``ask_engineer``).
    result_tools: list[str] = field(default_factory=lambda: ["ask_engineer"])
    texts: FallbackTexts | None = None
    #: Activity feed lines posted by the workflow itself (before the LLM runs).
    accepted_message: str = ""
    progress_message: str = ""
    #: n8n credential attached to every HTTP node of the service (``EXCEL_KEY_CRED``); None = no auth.
    service_credentials: dict[str, Any] | None = None
    #: Sticky note shown in the n8n editor after import.
    sticky_note: str = ""
    sticky_height: int = 360
    #: Namespace for stable node ids (keep the historical one when migrating an existing workflow).
    node_id_namespace: str = ""
    #: Fixed workflow id (default: derived from agent_id).
    workflow_id: str = ""
    example_objective: str = "Пример задачи"
    tool_grid_columns: int = 3
    max_iterations: int = 8
    #: LLM model id on the OpenAI-compatible endpoint.
    model: str = "qwen3.6-plus"

    # -- derived ----------------------------------------------------------------------------------

    @property
    def has_workflow(self) -> bool:
        return bool(self.system_prompt)

    @property
    def service_label(self) -> str:
        return self.service_title or self.title

    @property
    def health_url_key(self) -> str:
        """``urls.<key>`` in the Health Check: ``excel_tools_url`` → ``excel_tools_health``."""
        return re.sub(r"_url$", "", self.service_url_key) + "_health"

    @property
    def lab_host(self) -> str:
        """Compose DNS name of the lab service (``excel-tools``) — the Health Check flags it as lab-only."""
        return re.sub(r"^https?://", "", self.lab_url).split(":")[0].split("/")[0]

    @property
    def workflow_name(self) -> str:
        return f"Agent — {self.title}"

    @property
    def resolved_workflow_id(self) -> str:
        return self.workflow_id or workflow_id_for(self.agent_id)

    @property
    def output_filename(self) -> str:
        """``excel_extractor`` → ``excel-extractor-agent.workflow.json``; ``demo_agent`` → ``demo-agent.workflow.json``."""
        return re.sub(r"-agent$", "", self.agent_id.replace("_", "-")) + "-agent.workflow.json"

    def invoke(self) -> dict[str, Any]:
        if self.invoke_override is not None:
            return dict(self.invoke_override)
        if not self.has_workflow:
            raise ValueError(f"{self.agent_id}: HTTP-only agents need invoke_override")
        return {"kind": "n8n_workflow", "workflow_id": self.resolved_workflow_id, "workflow_name": self.workflow_name}

    def registry_row(self) -> dict[str, Any]:
        """The ``agent_registry`` row (column order of ``mas_agent_registry.COLUMN_NAMES``)."""
        return {
            "agent_id": self.agent_id,
            "title": self.title,
            "when_to_use": self.when_to_use,
            "input_required": list(self.input_required),
            "output_provides": list(self.output_provides),
            "invoke": self.invoke(),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "hitl_policy": self.hitl_policy,
            "enabled": self.enabled,
            "version": self.version,
        }

    def validate(self) -> None:
        if not self.agent_id.replace("_", "").isalnum() or not self.agent_id.islower():
            raise ValueError(f"agent_id must be snake_case: {self.agent_id!r}")
        if self.hitl_policy not in ("agent_asks", "never"):
            raise ValueError(f"{self.agent_id}: hitl_policy must be agent_asks or never")
        if self.has_workflow:
            for name in ("slug", "service_url_key", "rag_selector", "accepted_message", "progress_message"):
                if not getattr(self, name):
                    raise ValueError(f"{self.agent_id}: {name} is required for an agent with a workflow")
            if self.texts is None:
                raise ValueError(f"{self.agent_id}: texts (FallbackTexts) are required for an agent with a workflow")
            names = [t[0] for t in self.tools]
            if len(set(names)) != len(names):
                raise ValueError(f"{self.agent_id}: duplicate tool names")
            if "ask_engineer" not in names and self.hitl_policy == "agent_asks":
                raise ValueError(f"{self.agent_id}: hitl_policy=agent_asks needs an ask_engineer tool")
