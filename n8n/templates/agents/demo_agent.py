"""Agent — Demo Agent: the template agent (``agents-template/demo_agent``) and the live test of long jobs.

Copy this file for a new agent, rename ``agent_id`` / texts / tools, register it in ``agents/__init__.py``.
The registry row is seeded ``enabled=False`` so the planner never sees it in the field; the lab live
harness enables it through ``PUT /agents/demo_agent`` for its own case and disables it afterwards.
"""

from __future__ import annotations

from mas_agent_spec import AgentSpec, FallbackTexts

SYSTEM = """Ты — решатель агента Demo Agent (демонстрационный агент-шаблон). Ты читаешь задачу инженера и вызываешь один инструмент; считают инструменты, ты сам ничего не вычисляешь и SCHEDULE не пишешь.

session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Какой инструмент когда:
- Задача просит посчитать или перечислить скважины (в тексте есть их номера или есть факты предыдущих агентов) → count_wells. Один вызов.
- Задача просит запустить расчёт, прогон, долгую операцию или явно упоминает демонстрацию долгой работы → start_long_job с коротким русским названием расчёта в label. Один вызов, после ответа in_progress — STOP.
- Из задачи непонятно, что считать, и в ней нет скважин → ask_engineer одним вопросом обычной русской фразой.

Ответы инструментов:
- ok:false, code:no_wells_found — это тебе, не инженеру: спроси инженера через ask_engineer, о каких скважинах речь.
- ok:false, code:question_not_human — переформулируй вопрос прозой и вызови ask_engineer снова.
- status completed / in_progress / needs_input — результат зафиксирован, STOP.

Не придумывай скважины и числа — только из ответов инструментов. Заверши одним коротким фактическим предложением по-русски.
"""

TOOLS = [
    (
        "count_wells",
        "Посчитать скважины, упомянутые в задаче и в фактах предыдущих агентов. Детерминированно.",
        [("text", "string", False, "Текст для подсчёта; пусто — берётся задача инженера")],
    ),
    (
        "start_long_job",
        "Запустить долгий расчёт (имитация). Сразу возвращает in_progress; результат придёт позже через Activity.",
        [("label", "string", False, "Короткое русское название расчёта для ленты, например «Прогон демо-модели»")],
    ),
    (
        "ask_engineer",
        "Спросить инженера одним вопросом по-русски, когда из задачи непонятно, что считать. Не для ошибок вызова инструментов.",
        [
            ("question", "string", True, "Обычная русская фраза без имён полей и JSON"),
            ("options", "string", False, "Варианты через точку с запятой, как их видит инженер; пусто — свободный ответ"),
        ],
    ),
]

SPEC = AgentSpec(
    agent_id="demo_agent",
    title="Demo Agent",
    when_to_use=(
        "Демонстрационный агент-шаблон: считает скважины, упомянутые в задаче и в фактах других агентов, "
        "и умеет запускать долгий расчёт, результат которого приходит позже. Не читает файлы, не пишет SCHEDULE. "
        "Использовать только когда инженер прямо просит демонстрацию или подсчёт скважин."
    ),
    input_required=[],
    output_provides=["well_count", "wells"],
    input_schema={"handoff_message": "что посчитать или какой расчёт запустить"},
    output_schema={"data": {"well_count": "число скважин", "wells": "[имена скважин]"}},
    enabled=False,
    service_url_key="demo_agent_url",
    lab_url="http://demo-agent:8300",
    service_title="Demo Agent",
    slug="demo",
    system_prompt=SYSTEM,
    tools=TOOLS,
    # The generic tool protocol slice (opaque ids, clarification) — the agent works fine without cards.
    rag_selector="excel",
    rag_ready_note="Карточки — протокол инструментов (excel_protocol). Работай по задаче; про базу знаний инженера не спрашивай.",
    rag_empty_note="Срез протокола пуст или недоступен — работай по задаче и правилам инструментов.",
    result_tools=["count_wells", "start_long_job", "ask_engineer"],
    texts=FallbackTexts(
        no_result_question="Demo Agent не понял, что нужно сделать. Опишите задачу одним-двумя предложениями: посчитать скважины или запустить расчёт.",
        repeated_question="Demo Agent несколько раз пытался разобрать задачу и не смог. Опишите её одним-двумя предложениями.",
        done_message="Demo Agent выполнил задачу.",
        no_result_issue="no_demo_result",
        question_id="Q-demo",
        accepts_files=[],
        missing_input_message="Нет задачи для демонстрационного агента",
        missing_input_issue="missing_objective",
        missing_input_question="Опишите задачу: что именно посчитать или запустить.",
        no_result_failed_message="Demo Agent не вернул результат",
        no_result_failed_issue="demo_agent_no_result",
    ),
    accepted_message="Demo Agent принял задачу",
    progress_message="Demo Agent разбирает задачу и выбирает инструмент.",
    node_id_namespace="mas-demo-agent",
    example_objective="Посчитай скважины 1601, 1735 и 2012",
)
