"""Schedule Builder as a MAS agent — the ``AgentService`` the n8n workflow «Agent — Schedule Builder» talks to.

* ``open_session(agent_task)`` binds the case packet (baseline ``.INC``, upstream agent data, engineer
  answers) into a session and returns the inventory for the LLM (wells, keywords, dates, GRUPTREE).
* ``result(state)`` returns what the tools fixed; a dirty working text is built first.
* LLM tools live in ``agent_tools.py`` (``@agent.tools.tool``); parse/apply/emit — in the domain modules.

Same shape as ``agents-template/demo_agent/app/agent.py``.
"""

from __future__ import annotations

import os
from typing import Any

from mas_agent_kit import AgentService, SessionStore, ToolRegistry

from .io import bind_case_packet, commissioning_facts, file_ref, load_source
from .parse import parse_schedule, well_names
from .well_model import build_well_objects

AGENT_ID = "schedule_builder"
UNITS = "METRIC"
MAX_WELLS = 200

NO_APPLY_QUESTION = (
    "Schedule Builder не смог определить, что именно изменить в SCHEDULE. "
    "Опишите задачу подробнее: какие скважины, какие даты или режимы работы и откуда взять значения "
    "(Excel, текст, baseline)."
)
NO_SOURCE_REQUEST = {"question_id": "Q-sched", "question": "Приложите baseline .inc", "options": []}


def compact_inspect(source: str) -> dict[str, Any]:
    """Object inventory of a SCHEDULE for the LLM — never the ``.INC`` text itself."""
    doc = parse_schedule(source)
    wells = sorted(well_names(doc))
    well_objects = build_well_objects(doc)
    dates: list[str] = []
    keywords: list[str] = []
    groups: list[dict[str, str]] = []
    for block in doc.blocks:
        keywords.append(block.keyword)
        if block.keyword == "DATES":
            for rec in block.records:
                if rec.tokens:
                    dates.append(rec.tokens[0])
        if block.keyword == "GRUPTREE":
            for rec in block.records:
                if len(rec.tokens) >= 2:
                    groups.append({"child": rec.tokens[0].strip("'\""), "parent": rec.tokens[1].strip("'\"")})
    return {
        "well_count": len(wells),
        "wells": wells[:MAX_WELLS],
        "wells_truncated": len(wells) > MAX_WELLS,
        "well_objects": well_objects[:MAX_WELLS],
        "well_objects_truncated": len(well_objects) > MAX_WELLS,
        "keywords_present": list(dict.fromkeys(keywords)),
        "date_count": len(dates),
        "dates_preview": dates[:12],
        "gruptree_preview": groups[:40],
        "source_bytes": len(source.encode("utf-8")),
        "units": UNITS,
    }


class ScheduleBuilderAgent(AgentService):
    agent_id = AGENT_ID
    store = SessionStore(prefix="sch", app_name="schedule_builder")
    tools = ToolRegistry(store)
    activity_base_url = os.getenv("ACTIVITY_BASE_URL", "").rstrip("/")

    # -- session --------------------------------------------------------------------------------

    def open_session(self, task: dict[str, Any]) -> dict[str, Any]:
        inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
        context = task.get("context") if isinstance(task.get("context"), dict) else {}
        case_id = str(task.get("case_id") or "")
        task_id = str(task.get("task_id") or "")
        inputs, context = bind_case_packet(inputs, context, case_id, self.activity_base_url)
        try:
            source = load_source(inputs, case_id, self.activity_base_url)
        except Exception as exc:  # noqa: BLE001 — the reason goes to the engineer as text
            return self.not_opened(self._no_source(task_id, f"Нет исходного SCHEDULE: {exc}"))
        if not str(source).strip():
            return self.not_opened(self._no_source(task_id, "Нет исходного SCHEDULE"))
        facts = commissioning_facts(context, inputs)
        state = self.store.create(
            {
                "case_id": case_id,
                "task_id": task_id,
                "objective": str(task.get("objective") or ""),
                "handoff_message": str(task.get("handoff_message") or ""),
                "source_text": source,
                "working_text": source,
                "file_ref": file_ref(inputs),
                "inputs": inputs,
                "context": context,
                "facts": facts,
                "result": None,
            }
        )
        return self.opened(
            state,
            inspect=compact_inspect(source),
            fact_count=len(facts),
            facts_preview=[{"well": row.get("well"), "date": row.get("date")} for row in facts[:40]],
        )

    def result(self, state: dict[str, Any]) -> dict[str, Any]:
        result = state.get("result")
        if not (isinstance(result, dict) and result.get("status")):
            working = str(state.get("working_text") or "")
            if working.strip() and working != str(state.get("source_text") or ""):
                self.tools.run(state, "build_schedule", {})
                result = state.get("result")
        if isinstance(result, dict) and result.get("status"):
            return result
        return self.new_result(
            state,
            "needs_input",
            "Schedule Builder не внёс изменений: не хватает данных, чтобы понять задачу.",
            issues=[{"type": "no_apply"}],
            requests=[{"question_id": "Q-apply", "question": NO_APPLY_QUESTION, "options": [], "accepts": {"free_text": True, "files": ["xlsx", ".inc"]}}],
        )

    # -- results --------------------------------------------------------------------------------

    def new_result(self, state: dict[str, Any], status: str, message: str, **kw: Any) -> dict[str, Any]:
        """Every Builder result states its units unless the caller lists other assumptions."""
        kw.setdefault("assumptions", [{"units": UNITS}])
        return super().new_result(state, status, message, **kw)

    def store_result(self, state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """Save the full result; echo to the LLM only sizes of the artifacts — the ``.INC`` never goes to the model."""
        super().store_result(state, result)
        arts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
        compact = {k: v for k, v in result.items() if k != "artifacts"}
        compact["artifacts"] = {
            "schedule_out": bool(str(arts.get("schedule_out") or "").strip()),
            "diff_bytes": len(str(arts.get("diff") or "").encode("utf-8")),
        }
        return compact

    def _no_source(self, task_id: str, message: str) -> dict[str, Any]:
        return self.new_result({"task_id": task_id}, "needs_input", message, issues=[{"type": "missing_schedule_source"}], requests=[dict(NO_SOURCE_REQUEST)], assumptions=[])


agent = ScheduleBuilderAgent()

from . import agent_tools  # noqa: E402,F401  — registers the LLM tools on ``agent.tools``
