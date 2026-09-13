"""Agent — Schedule Builder: deterministic SCHEDULE tools (parse/apply/emit); the LLM only picks the tool and its arguments."""

from __future__ import annotations

import json

from mas_agent_spec import AgentSpec, FallbackTexts
from schedule_rag_workflows import KEYWORDS as SCHEDULE_KEYWORDS

SYSTEM = """Ты — инженер-решатель агента Schedule Builder: читаешь задачу инженера, выбираешь инструмент и вызываешь его со структурированными аргументами. Текст SCHEDULE (.INC) пишут инструменты, не ты.

Исходный .INC тебе не показывают — он лежит в сессии FastAPI. session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Какой инструмент когда:
- Задача про НОВЫЕ ДАТЫ ВВОДА скважин (Excel «скважина — дата», fact_count > 0) → apply_commissioning. Факты, решение по скважинам вне Excel и параметры новых скважин уже в сессии — аргументов не нужно. Другие apply_* для такой задачи не вызывай.
- Задача про ГРУППЫ (поместить скважины в группу, групповой контроль GCONPROD) → inspect_schedule (имена скважин, дерево GRUPTREE), затем apply_group_rebind с полным spec: wells, parent_group, parent_of_parent, control (ORAT/WRAT/GRAT/LRAT/RESV), gas_rate числом в м3/сут («200 тыс. м3 газа в сут.» → 200000). Родителя новой группы бери из дерева исходного файла (корень — FIELD или как в inspect), если инженер не сказал иначе.
- В сессии есть именованные наборы (dataset_count > 0, не пары «скважина — дата ввода»): inspect_dataset по имени → search_keywords / get_keyword (имя keyword только из каталога и retrieved knowledge) → apply_dataset с field_map (поле набора → параметр keyword из get_keyword.details.parameters). Сопоставление полей — твоё, по смыслу набора и схемы; не выдумывай keyword и не подставляй пустые значения. apply_commissioning сюда не подходит.
- Точечные правки режимов/keywords без набора → search_keywords → get_keyword (details.parameters) → apply_operations или render_ir.
- inspect_well / analyze_forecast_controls / list_records — чтобы посмотреть скважину перед правкой. Не вызывай их «на всякий случай» и не больше трёх раз подряд.
- build_schedule — только если apply уже менял сессию; validate_result — проверки emit.
- ask_engineer — единственный способ спросить инженера. Только когда данных нет ни в задаче, ни в исходном файле, ни в ответах инженера (engineer_answers), ни в наборе. Один вопрос обычной русской фразой: что нужно и зачем; варианты (options) — как их называет инженер: имена групп из исходного файла, «оставить»/«убрать». Никаких имён полей, JSON, enum, кодов. Инженер отвечает фактами, таблицами и файлами — не строками .INC.

Ответы инструментов:
- ok:false, code:spec_incomplete — это тебе, не инженеру: заполни missing из текста задачи, inspect_schedule и inspect_dataset (where_to_find подсказывает откуда) и вызови инструмент снова. Спрашивай инженера, только если данных действительно нет.
- ok:false, code:dataset_values_missing — в наборе пустые обязательные поля: спроси инженера через ask_engineer, не подставляй значения сам.
- ok:false, code:question_not_human — переформулируй вопрос прозой и вызови ask_engineer снова.
- ok:false, code:well_not_in_schedule / operations_required / unknown_dataset / unknown_keyword — ошибка твоего вызова; исправь аргументы.
- ok:false, code:result_already_stored — результат этого запуска уже зафиксирован (apply или вопрос инженеру). Больше инструменты не вызывай, заверши ответ.
- status completed или needs_input от apply_* / ask_engineer — результат зафиксирован. STOP: не вызывай build и другие apply.

Инварианты:
- Не придумывай скважины, даты, группы, дебиты. Имена скважин — только из inspect_schedule.
- Если комментарий WCONPROD содержит «факт»/«fact», запись фактическая: её нельзя удалять, переносить или считать прогнозным якорем ввода. Якорь commissioning — первый нефактический WCONPROD; следующие WCONPROD — прогнозные режимы, они сохраняются.
- Один параметр после существующего контроля — WELTARG, не переписывание WCONPROD. Не путай WECON (экономика), WTEST (переоткрытие), WELOPEN (статус), WEFAC (uptime), WPIMULT (CF), GCONPROD (группа).
- Имена полей — из get_keyword.details (WELL, DATE, CHILD). WCONPROD variant = CONTROL в нижнем регистре (orat, wrat, grat, lrat, bhp, thp, resv, grup); если variant не указан — положи CONTROL в fields.
- apply_operations принимает JSON-массив [{keyword, operation, fields}], не объект с ключами "0","1".
- Если analyze_forecast_controls вернул needs_input по границе history/forecast — не применяй операцию, спроси инженера.
- rework_reason в задаче — замечание оркестратора к прошлому результату: устрани именно его.
- Retrieved knowledge — только срез schedule_mvp (keyword_instruction / worked_example): when-to-use и pitfalls. Расклад полей — из get_keyword.details / render_ir. Пустой или unavailable срез — работай инструментами, не спрашивай про базу знаний.

Заверши одним коротким фактическим предложением по-русски о том, что сделано или чего не хватило.
"""

TOOLS = [
    ("inspect_schedule", "Объектная инвентаризация исходного SCHEDULE: wells, factual/forecast WCONPROD, commissioning anchors, история режимов, keywords, даты и GRUPTREE. Без полного .INC.", []),
    (
        "inspect_well",
        "Подробно осмотреть одну скважину: identity, factual WCONPROD, commissioning anchor (первый нефактический WCONPROD), последующие forecast control events и связанные records.",
        [("well", "string", True, "Точное имя скважины из inspect_schedule")],
    ),
    (
        "analyze_forecast_controls",
        "Детерминированно разобрать timeline одной скважины: history/forecast controls, commissioning, WELTARG, WECON, WTEST, WELOPEN, WEFAC, WPIMULT и правила выбора keyword.",
        [("well", "string", True, "Точное имя скважины из inspect_schedule")],
    ),
    (
        "search_keywords",
        "Найти keyword в каталоге по фразе задачи или описания набора: имя, описание, поля схемы. Не словарь синонимов. Имя keyword не выдумывать.",
        [("intent", "string", True, "Фраза из задачи или заголовок/смысл набора")],
    ),
    (
        "get_keyword",
        "Объект keyword: details.kind=schedule_keyword, parameters[{name,position,type,required,unit,description,enum}]. Имена полей только отсюда.",
        [("keyword", "string", True, "DATES / WCONPROD / GRUPTREE / ...")],
    ),
    (
        "inspect_dataset",
        "Строки и поля именованного набора, уже лежащего в сессии (из Excel). Keyword не выбирает.",
        [("name", "string", True, "Имя набора из open_session.datasets")],
    ),
    (
        "list_records",
        "Компактные records keyword. well опционален. Не больше 40 строк.",
        [
            ("keyword", "string", True, "Имя keyword"),
            ("well", "string", False, "Точное имя скважины из inspect"),
        ],
    ),
    (
        "apply_commissioning",
        "Сдвинуть даты ввода скважин по фактам «скважина — дата» из Excel, уже лежащим в сессии (fact_count). Решение по скважинам вне Excel и параметры новых скважин тоже берутся из сессии. Аргументов нет. Для задач про даты ввода — это единственный нужный apply.",
        [],
    ),
    (
        "apply_dataset",
        "Записать именованный набор в SCHEDULE. Keyword — из search_keywords/get_keyword/карточки знаний, не выдумывать. field_map — поле набора → параметр схемы. Пустые значения не подставляй.",
        [
            ("dataset", "string", True, "Имя набора из inspect_dataset / open_session.datasets"),
            ("keyword", "string", True, "Имя keyword из каталога (search_keywords / get_keyword)"),
            ("field_map", "json", True, "Объект: поле набора → параметр keyword из get_keyword.details.parameters"),
            ("date_field", "string", False, "Поле набора с датой шага DATES, если строки привязаны к дате исходного файла; пусто — обновить существующие записи скважины"),
        ],
    ),
    (
        "apply_group_rebind",
        "Поместить скважины в группу с групповым контролем (WELSPECS + GRUPTREE + GCONPROD). Spec заполняешь ты из задачи и inspect_schedule; ничего не выводится из текста автоматически. Неполный spec вернётся как ok:false spec_incomplete с missing и where_to_find — дополни и вызови снова.",
        [
            ("wells", "string", True, "Имена скважин через пробел или запятую, только из inspect_schedule.wells"),
            ("parent_group", "string", True, "Имя целевой группы из задачи (например DKS)"),
            ("parent_of_parent", "string", False, "Родитель целевой группы в GRUPTREE: корень дерева исходного файла (FIELD) или группа из задачи. Пусто — возьмётся единственный корень из исходного файла"),
            ("control", "string", True, "Тип группового контроля: GRAT (газ), ORAT (нефть), WRAT (вода), LRAT (жидкость), RESV"),
            ("gas_rate", "number", True, "Целевой дебит числом в м3/сут: «200 тыс. м3 в сут.» → 200000"),
            ("effective_at", "string", False, "Дата начала контроля вида 1 JAN 2026; пусто — с даты ввода этих скважин"),
        ],
    ),
    (
        "ask_engineer",
        "Задать инженеру ОДИН вопрос обычной русской фразой, когда данных нет ни в задаче, ни в исходном файле, ни в engineer_answers. options — варианты словами инженера (имена групп из исходного файла, «оставить»/«убрать»). Без имён полей, JSON, enum. После вызова — STOP.",
        [
            ("question", "string", True, "Вопрос по-русски: что нужно и зачем, с именами скважин/групп"),
            ("options", "string", False, "Варианты ответа через точку с запятой, например «Оставить как в исходном файле; Убрать из прогноза». Пусто — свободный ответ"),
            ("topic", "string", False, "Короткий латинский идентификатор темы вопроса, например target_group"),
            ("accepts_files", "string", False, "Какие файлы принимаются, через запятую: xlsx, .inc"),
        ],
    ),
    (
        "apply_operations",
        "Применить operations. JSON-массив: [{\"keyword\":\"WCONPROD\",\"operation\":\"MODIFY\",\"fields\":{...}}].",
        [("operations", "json", True, "Массив {keyword, operation, fields}. Не объект с ключами 0,1,2.")],
    ),
    (
        "render_ir",
        "Собрать текст keyword по schema_catalogue: ir_events[{event_id,operation,keyword,variant,fields,provenance}]. Не пиши .INC руками.",
        [
            ("mode", "string", False, "CREATE или REVISE"),
            ("ir_events", "json", True, "Массив IR-событий с fields по get_keyword.details.parameters"),
        ],
    ),
    (
        "build_schedule",
        "Собрать текущий working SCHEDULE, если apply уже отработал. Не вызывай вместо commissioning/group_rebind.",
        [],
    ),
    (
        "validate_result",
        "Проверить текущий working text: findings без полного файла.",
        [],
    ),
]

SPEC = AgentSpec(
    agent_id="schedule_builder",
    title="Schedule Builder",
    when_to_use=(
        "Исходный SCHEDULE (.inc): сдвиг дат ввода по фактам «скважина — дата»; добавление новых скважин по их "
        "параметрам; перепривязка скважин в группу (GRUPTREE/GCONPROD); запись именованного набора из Excel в keyword "
        "по схеме (карточка знаний + get_keyword, не словарь в коде). Факты, наборы и параметры новых скважин берёт из "
        "результатов агента, который читал Excel, и из ответов инженера — сам Excel не читает; для перепривязки групп "
        "Excel не нужен. Не выдумывает keyword, даты и имена, которых нет в задаче, наборе или исходном файле. Отдаёт "
        "новый .INC и список изменений."
    ),
    input_required=["schedule_source"],
    output_provides=["schedule_out", "diff"],
    input_schema={
        "artifacts": {"schedule_source": "исходный SCHEDULE .inc и его INCLUDE-файлы"},
        "data": {
            "facts": "даты ввода «скважина — дата» (нужны для новых дат ввода)",
            "new_wells": "параметры новых скважин (нужны, если добавляются скважины)",
            "datasets": "именованные наборы строк из Excel (нужны, если задача — не даты ввода и не перепривязка, а другая таблица в SCHEDULE)",
        },
        "handoff_message": "что изменить в SCHEDULE и на основании чего",
    },
    output_schema={"artifacts": {"schedule_out": "новый SCHEDULE .inc", "diff": "изменения относительно исходного файла"}},
    service_url_key="schedule_service_url",
    lab_url="http://schedule-builder:8090",
    slug="schedule",
    system_prompt=SYSTEM,
    tools=TOOLS,
    rag_selector="schedule",
    rag_ready_note=(
        "Карточки — срез schedule_mvp (keyword_instruction / worked_example), "
        "не excel_protocol и не orchestrator_routing. "
        "Это when-to-use и pitfalls. Расклад полей — get_keyword.details / render_ir, "
        "не schema_catalogue из RAG."
    ),
    rag_empty_note=(
        "Срез schedule_mvp пуст или недоступен — работай инструментами. "
        "Не спрашивай HITL про RAG и не ходи в другие target_base."
    ),
    # Keyword families / topics narrow the RAG slice to the cards about the keywords the task names.
    retrieval_filters_js="const ALLOWED_KEYWORDS=" + json.dumps(SCHEDULE_KEYWORDS) + ";\n" + r"""
const allowed=new Set(ALLOWED_KEYWORDS);
for(const k of (blob.match(/\b[A-Z][A-Z0-9_]{2,}\b/g)||[])) if(allowed.has(k)) keyword_families.push(k);
""",
    planner_extra_js=(
        "fact_count:opened.fact_count||0,\n"
        "  facts_preview:opened.facts_preview||[],\n"
        "  dataset_count:opened.dataset_count||0,\n"
        "  datasets:opened.datasets||[]"
    ),
    result_tools=["apply_", "build_", "ask_engineer"],
    texts=FallbackTexts(
        no_result_question=(
            "Schedule Builder не внёс изменений в SCHEDULE: не хватило данных, чтобы понять задачу. "
            "Опишите, что именно нужно изменить (скважины, даты, режимы) и откуда взять значения (Excel, текст, исходный файл)."
        ),
        repeated_question=(
            "Schedule Builder не смог продвинуться по задаче: несколько раз проверял SCHEDULE, но не понял, что именно изменить. "
            "Опишите задачу подробнее: какие скважины, какие даты или режимы работы и откуда взять значения."
        ),
        done_message="Schedule Builder завершил работу с инструментами.",
        no_result_issue="no_apply",
        question_id="Q-apply",
        accepts_files=["xlsx", ".inc"],
        missing_input_message="Нет исходного SCHEDULE",
        missing_input_issue="missing_schedule_source",
        missing_input_question="К задаче не приложен исходный файл schedule. Приложите прогнозный .inc, который нужно изменить.",
        no_result_failed_message="Schedule Builder не вернул SCHEDULE",
        no_result_failed_issue="schedule_agent_no_result",
    ),
    accepted_message="Schedule Builder принял задачу и анализирует исходный schedule.",
    progress_message="Schedule Builder проверяет структуру скважин, даты и прогнозные controls.",
    node_id_namespace="mas-sched-agent",
    workflow_id="c8d5f3b2-6e91-5d22-8a7b-1f0c4e9a2d55",
    example_objective="Сдвинь даты ввода по Excel",
    tool_grid_columns=3,
)
