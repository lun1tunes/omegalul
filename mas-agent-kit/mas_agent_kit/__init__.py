"""mas_agent_kit — shared core of NOVATEK RE MASter agent services.

    from mas_agent_kit import AgentService, SessionStore, ToolRegistry, ToolError, agent_router

One place for: session storage, the tool registry and error envelope, ``agent_result`` builders,
HITL helpers (``human_text_problems``, ``ask_engineer`` validation), the Activity client and the
FastAPI router that every agent service exposes. Domain logic (Excel, SCHEDULE, …) stays in the
service that owns it.

This is a plain module of the repository, not a pip package: a service makes it importable by adding
``<repo>/mas-agent-kit`` to ``sys.path`` in its ``app/__init__.py`` (see ``agents-template/demo_agent``).
Runtime needs: ``fastapi`` and ``filelock`` (``requirements.txt`` next to this folder).
"""

from .activity import ActivityClient, compact_for_log
from .agent import AgentService, agent_router, create_agent_app
from .dataset import dataset_entry, dataset_rows, expected_names, expected_output, is_dataset, upstream_datasets
from .errors import SessionNotFound, ToolError, error_envelope
from .hitl import engineer_answers, engineer_request_from_args, hitl_payloads
from .packet import CasePacket, flatten_artifacts, upstream_agent_data
from .result import STATUSES, agent_result, engineer_request, in_progress, needs_input, result_text_problems
from .session import SessionStore
from .text import human_text_problems, list_preview, options_for_human, parse_jsonish, plural_ru, slug
from .tools import ToolContext, ToolRegistry, tool_schema

__all__ = [
    "STATUSES",
    "ActivityClient",
    "AgentService",
    "CasePacket",
    "SessionNotFound",
    "SessionStore",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "agent_result",
    "agent_router",
    "compact_for_log",
    "create_agent_app",
    "dataset_entry",
    "dataset_rows",
    "engineer_answers",
    "engineer_request",
    "engineer_request_from_args",
    "error_envelope",
    "expected_names",
    "expected_output",
    "flatten_artifacts",
    "hitl_payloads",
    "human_text_problems",
    "is_dataset",
    "list_preview",
    "in_progress",
    "needs_input",
    "options_for_human",
    "parse_jsonish",
    "plural_ru",
    "result_text_problems",
    "slug",
    "tool_schema",
    "upstream_agent_data",
    "upstream_datasets",
]
