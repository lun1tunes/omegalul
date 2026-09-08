"""Demo Agent — the template for a new MAS agent on ``mas-agent-kit``: the ``AgentService`` class.

What it does (on purpose trivial, so the plumbing is the point):

* ``count_wells``   — counts well names in the task text and in facts of upstream agents; ``completed``.
* ``start_long_job`` — a "long calculation": returns ``in_progress`` right away, posts ``agent.progress``
  lines while it runs and, when done, hands the final ``agent_result`` to the orchestrator through
  Activity (``ActivityClient.finish_task``). This is how hour-long agents work in the MAS.
* ``ask_engineer``  — one Russian question to the engineer (``needs_input``).

Tools here are closures inside ``_register_tools`` (the agent is small); a bigger agent keeps them in
``app/agent_tools.py`` as ``@agent.tools.tool`` functions — see ``schedule-builder-service`` and
``excel-agent-tools``. Copy this folder, rename ``agent_id`` / tools / texts, add the ``AgentSpec`` in
``n8n/templates/agents/<agent_id>.py`` — the orchestrator is not touched (see README.md).
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from mas_agent_kit import (
    ActivityClient,
    AgentService,
    SessionNotFound,
    SessionStore,
    ToolError,
    ToolRegistry,
    engineer_request_from_args,
    in_progress,
    plural_ru,
)

AGENT_ID = "demo_agent"
# Lab default: 40 s of "calculation" with a progress line every 15 s. Field: set in the service env.
JOB_SECONDS = float(os.getenv("DEMO_JOB_SECONDS", "40"))
PROGRESS_EVERY_S = float(os.getenv("DEMO_PROGRESS_EVERY_S", "15"))
# A well name in engineering texts: 3–5 digits with an optional letter suffix (1601, 1735, 2012G).
_WELL_TOKEN = re.compile(r"(?<![\w.])(\d{3,5}[A-Za-zА-Яа-я]?)(?![\w.])")


def wells_in(text: str) -> list[str]:
    """Deterministic helper: unique well-like tokens in reading order."""
    seen: list[str] = []
    for token in _WELL_TOKEN.findall(str(text or "")):
        if token not in seen:
            seen.append(token)
    return seen


class DemoAgent(AgentService):
    agent_id = AGENT_ID

    def __init__(self, root: str | None = None, *, job_seconds: float = JOB_SECONDS, progress_every_s: float = PROGRESS_EVERY_S):
        self.store = SessionStore(prefix="demo", root=root or os.getenv("DEMO_SESSIONS_DIR") or "data/sessions")
        self.tools = ToolRegistry(self.store)
        self.job_seconds = job_seconds
        self.progress_every_s = progress_every_s
        self.jobs: dict[str, threading.Thread] = {}
        self._register_tools()

    # -- session --------------------------------------------------------------------------------

    def open_session(self, task: dict[str, Any]) -> dict[str, Any]:
        packet = self.packet(task)
        if not packet.objective.strip():
            return self.not_opened(self.needs_input({"task_id": packet.task_id}, "Опишите задачу: что именно нужно посчитать или запустить."))
        state = self.store.create({**self.base_state(packet), "upstream_facts": packet.upstream_data().get("facts") or []})
        # ``inspect`` is what the LLM sees before choosing a tool — keep it short and factual.
        return self.opened(state, inspect={"wells_in_task": wells_in(packet.objective), "upstream_fact_count": len(state["upstream_facts"])})

    def result(self, state: dict[str, Any]) -> dict[str, Any]:
        return state.get("result") or self.needs_input(state, "Демо-агент не понял, что нужно сделать. Опишите задачу одним-двумя предложениями.")

    # -- tools ----------------------------------------------------------------------------------

    def _register_tools(self) -> None:
        @self.tools.tool(
            "count_wells",
            "Посчитать скважины, упомянутые в тексте задачи и в фактах предыдущих агентов.",
            {"text": {"type": "string", "description": "Текст для подсчёта; по умолчанию — задача инженера."}},
        )
        def count_wells(ctx, args):
            text = str(args.get("text") or ctx.state.get("objective") or "")
            wells = wells_in(text)
            for fact in ctx.state.get("upstream_facts") or []:
                well = str(fact.get("well") or "") if isinstance(fact, dict) else ""
                if well and well not in wells:
                    wells.append(well)
            if not wells:
                raise ToolError("no_wells_found", "В тексте нет имён скважин — уточни у инженера через ask_engineer, о каких скважинах речь.", text_preview=text[:120])
            message = f"Насчитал {plural_ru(len(wells), 'скважину', 'скважины', 'скважин')}: {', '.join(wells[:12])}."
            self.store_result(ctx.state, self.new_result(ctx.state, "completed", message, data={"well_count": len(wells), "wells": wells}))
            return {"status": "completed", "well_count": len(wells), "wells": wells}

        @self.tools.tool(
            "start_long_job",
            "Запустить долгий расчёт (имитация): агент сразу вернёт статус «в работе», а результат придёт позже через Activity.",
            {"label": {"type": "string", "description": "Как назвать расчёт в ленте (по-русски)."}},
        )
        def start_long_job(ctx, args):
            label = str(args.get("label") or "Демонстрационный расчёт").strip().rstrip(".")
            label = label[:1].upper() + label[1:]  # the LLM tends to send lowercase labels; feed lines are sentences
            minutes = max(1, round(self.job_seconds / 60))
            message = f"{label} запущен, ориентировочно {plural_ru(minutes, 'минута', 'минуты', 'минут')}."
            self.store_result(
                ctx.state,
                in_progress(self.agent_id, str(ctx.state.get("task_id") or ""), message, watch={"kind": "timer", "ref": ctx.session_id, "poll_hint": f"{int(self.progress_every_s)}s"}),
            )
            self._spawn_job(dict(ctx.state), label)
            return {"status": "in_progress", "message": message}

        @self.tools.tool(
            "ask_engineer",
            "Задать инженеру один вопрос по-русски, если без ответа задачу не решить.",
            {"question": {"type": "string"}, "options": {"type": "string", "description": "Варианты через точку с запятой (необязательно)."}},
            required=["question"],
        )
        def ask_engineer(ctx, args):
            request = engineer_request_from_args(args, default_topic="demo", default_files=())
            result = self.new_result(ctx.state, "needs_input", request["question"], requests=[request], issues=[{"type": "engineer_input_required", "topic": request["question_id"]}])
            self.store_result(ctx.state, result)
            return {"status": "needs_input", "question_id": request["question_id"]}

    # -- the long job ---------------------------------------------------------------------------

    def _spawn_job(self, state: dict[str, Any], label: str) -> None:
        thread = threading.Thread(target=self.run_job, args=(state, label), daemon=True, name=f"demo-job-{state.get('session_id')}")
        self.jobs[str(state.get("session_id"))] = thread
        thread.start()

    def run_job(self, state: dict[str, Any], label: str) -> dict[str, Any]:
        """Runs outside the HTTP request: progress lines to the feed, then the final result to the orchestrator."""
        activity: ActivityClient = self.activity_for_state(state)
        started = time.monotonic()
        wells = wells_in(str(state.get("objective") or ""))
        while True:
            left = self.job_seconds - (time.monotonic() - started)
            if left <= 0:
                break
            time.sleep(min(self.progress_every_s, left))
            elapsed = int(time.monotonic() - started)
            if elapsed < self.job_seconds:
                activity.progress(f"{label}: выполняется, прошло {plural_ru(elapsed, 'секунда', 'секунды', 'секунд')}.", status="waiting_agent")
        checksum = sum(int(re.sub(r"\D", "", w) or 0) for w in wells)
        message = f"{label} завершён: обработано {plural_ru(len(wells), 'скважина', 'скважины', 'скважин')}, контрольная сумма {checksum}."
        final = self.new_result(state, "completed", message, data={"well_count": len(wells), "wells": wells, "seconds": int(self.job_seconds)})
        try:
            with self.store.lock(str(state["session_id"])):
                self.store_result(self.store.load(str(state["session_id"])), final)
        except SessionNotFound:
            pass  # the workflow already closed the session — the result still reaches the orchestrator
        activity.finish_task(final)
        return final


agent = DemoAgent()
