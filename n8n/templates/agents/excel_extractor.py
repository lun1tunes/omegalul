"""Agent — Excel Extractor: reads the case's Excel books, the LLM picks table/columns, tools extract deterministically."""

from __future__ import annotations

import uuid

from mas_agent_spec import EXCEL_KEY_CRED, AgentSpec, FallbackTexts

SYSTEM = """Ты — решатель агента Excel Extractor: читаешь задачу инженера и инвентарь приложенных Excel-книг (файлы, листы, таблицы с колонками и первыми строками), сам выбираешь таблицу и колонки и вызываешь инструмент извлечения. Данные извлекают инструменты — ты их не переписываешь и SCHEDULE / .INC не пишешь.

Книгу целиком тебе не показывают — она лежит в сессии FastAPI. session_id привязан workflow: никогда не передавай session_id и обёртку args/input.

Какой инструмент когда:
- Задача про даты ввода / запуска скважин (таблица «скважина — дата») → extract_commissioning с table_id, well_column и date_column из инвентаря (inspect.tables: columns и sample). Если в таблице несколько колонок с датами — бери новую (плановую) дату ввода, а не baseline / старую / дату из .INC. Один вызов на таблицу.
- В инвентаре есть таблица параметров скважин (группа, интервал MD, диаметр, режим, дебит, BHP, VFP, файл траектории) и задача упоминает новые скважины или их добавление → extract_well_parameters с table_id, well_column и mapping колонок на поля: date, group, phase, i, j, md_top, md_bot, diameter, control, rate, bhp, thp, vfp_table, welltrack_include. Немаппированные колонки сохраняются под своими заголовками.
- Обе таблицы есть → оба инструмента, по одному вызову каждый.
- Инвентаря не хватает (заголовки непонятны, таблица не найдена) → describe_table / sheet_preview / list_column_values / detect_tables / match_tables — не больше трёх вызовов подряд.
- Другие данные (дебиты, история, режимы, PVT) → detect_tables / describe_table / query_table. select и filters в query_table — JSON-массивы, не объект с ключами "0","1". Не выгружай всю книгу.
- ask_engineer — единственный способ спросить инженера: только когда по инвентарю и ответам инженера (engineer_answers) нельзя выбрать таблицу или колонку (например две колонки дат без пояснений). Один вопрос обычной русской фразой; варианты — как их видит инженер (названия листов и колонок). Никаких имён полей, JSON, enum.

Ответы инструментов:
- ok:false, error:spec_incomplete / table_not_found / column_not_found / column_not_dates / no_rows — это тебе, не инженеру: исправь аргументы по инвентарю (available_tables / available_columns / missing в том же ответе подсказывают) и вызови инструмент снова.
- ok:false, error:question_not_human — переформулируй вопрос прозой и вызови ask_engineer снова.
- ok:false, error:too_many_attempts — больше этот инструмент не вызывай: спроси инженера или заверши ответ.
- status completed от extract_* — результат зафиксирован. Если извлекать больше нечего — STOP. status needs_input от ask_engineer — STOP.

Инварианты:
- Не придумывай скважины, даты, дебиты, имена листов, table_id и колонок — только из инвентаря и ответов инструментов.
- rework_reason в задаче — замечание оркестратора к прошлому результату: устрани именно его.
- Retrieved knowledge — только срез excel_protocol / protocol_instruction (протокол инструментов). Пустой или unavailable срез — работай по инвентарю, инженера про базу знаний не спрашивай.

Заверши одним коротким фактическим предложением по-русски: что извлечено или чего не хватило.
"""

TOOLS = [
    ("workbook_introspect", "Компактные листы и размеры книги. Без файла.", []),
    (
        "sheet_preview",
        "Небольшой preview одного листа. sheet только из инвентаря / introspect.",
        [("sheet", "string", True, "Точное имя листа из инвентаря")],
    ),
    (
        "detect_tables",
        "Найти таблицы (если инвентарь пуст или неполный). sheet опционален. Возвращает table_id, range, columns — не книгу.",
        [("sheet", "string", False, "Ограничить поиск одним листом")],
    ),
    (
        "match_tables",
        "Ранжировать найденные таблицы по фразе запроса.",
        [("query", "string", True, "Что искать: даты ввода, параметры скважин, дебиты, ...")],
    ),
    (
        "describe_table",
        "Колонки, число строк и sample одной таблицы. table_id только из инвентаря / detect.",
        [("table_id", "string", True, "table_id из инвентаря")],
    ),
    (
        "list_column_values",
        "Ограниченные distinct-значения одной колонки — чтобы понять, что в ней лежит.",
        [
            ("table_id", "string", True, "table_id из инвентаря"),
            ("column", "string", True, "Точное имя колонки из инвентаря"),
        ],
    ),
    (
        "query_table",
        "Строки таблицы для прочих данных (дебиты, история). select и filters — JSON-массивы: [\"well\",\"date\"] и [{\"field\":\"well\",\"operator\":\"eq\",\"value\":\"101\"}].",
        [
            ("table_id", "string", True, "table_id из инвентаря"),
            ("select", "json", False, "Массив имён колонок, например [\"well\",\"date\"]"),
            ("filters", "json", False, "Массив {field, operator, value}"),
            ("limit", "string", False, "Лимит строк, по умолчанию 200"),
        ],
    ),
    (
        "extract_commissioning",
        "Извлечь факты «скважина — дата ввода» из выбранной тобой таблицы. Ты указываешь table_id и точные имена колонок из инвентаря; извлечение детерминированное, колонки проверяются (column_not_found / column_not_dates вернутся тебе).",
        [
            ("table_id", "string", True, "table_id таблицы со скважинами и датами из инвентаря"),
            ("well_column", "string", True, "Точное имя колонки со скважинами"),
            ("date_column", "string", True, "Точное имя колонки с новой датой ввода (не baseline)"),
        ],
    ),
    (
        "extract_well_parameters",
        "Извлечь таблицу параметров новых скважин (группа, интервал MD, диаметр, режим, дебит, BHP, VFP, файл траектории). Ты указываешь table_id, колонку скважин и mapping колонок на поля; извлечение детерминированное.",
        [
            ("table_id", "string", True, "table_id таблицы параметров из инвентаря"),
            ("well_column", "string", True, "Точное имя колонки со скважинами"),
            (
                "mapping",
                "json",
                False,
                "JSON-объект {поле: колонка}: date, group, phase, i, j, md_top, md_bot, diameter, control, rate, bhp, thp, vfp_table, welltrack_include → точные имена колонок",
            ),
        ],
    ),
    (
        "ask_engineer",
        "Спросить инженера одним вопросом по-русски, когда по инвентарю нельзя выбрать таблицу или колонку. Варианты показываются кнопками. Не для ошибок вызова инструментов.",
        [
            ("question", "string", True, "Обычная русская фраза: что нужно уточнить и зачем, без имён полей и JSON"),
            ("options", "string", False, "Варианты через точку с запятой, как их видит инженер (названия листов/колонок); пусто — свободный ответ"),
            ("topic", "string", False, "Короткая латинская тема вопроса, например date_column"),
        ],
    ),
]

NO_EXTRACT = (
    "Excel Extractor не смог понять, какие данные взять из приложенной книги. "
    "Уточните, на каком листе и в каких колонках находятся скважины и нужные значения (даты ввода, режимы, дебиты)."
)

SPEC = AgentSpec(
    agent_id="excel_extractor",
    title="Excel Extractor",
    when_to_use=(
        "Читает книги Excel, приложенные к задаче, и извлекает из них факты: даты ввода скважин "
        "(пары «скважина — дата»), параметры новых скважин (группа, интервал MD, диаметр, режим и дебит, BHP, VFP, "
        "траектория) и другие табличные данные. Сам выбирает лист и колонки; если таблица неоднозначна — спрашивает "
        "инженера. Не пишет SCHEDULE и не меняет файлы. Нужен хотя бы один приложенный Excel."
    ),
    input_required=["excel"],
    output_provides=["facts", "new_wells"],
    input_schema={
        "artifacts": {"excel": "книга Excel инженера (одна или несколько)"},
        "handoff_message": "какие факты извлечь (даты ввода, параметры новых скважин, …)",
    },
    output_schema={
        "data": {
            "facts": "[{well, date}] — даты ввода скважин",
            "new_wells": "[{well, group, …}] — параметры новых скважин",
        }
    },
    service_url_key="excel_tools_url",
    lab_url="http://excel-tools:8000",
    service_title="Excel Tools",
    slug="excel",
    system_prompt=SYSTEM,
    tools=TOOLS,
    rag_selector="excel",
    rag_ready_note=(
        "Карточки — срез excel_protocol (protocol_instruction), не schedule_mvp и не orchestrator_routing. "
        "Это протокол инструментов (opaque id, query_table, clarification). "
        "Строки workbook только из Excel-tools. Не спрашивай HITL про базу знаний."
    ),
    rag_empty_note=(
        "Срез excel_protocol пуст или недоступен — работай правилами инструментов. "
        "Не спрашивай HITL про RAG и не ходи в другие target_base."
    ),
    retrieval_filters_js="""
if(/дат|ввод|commission/.test(low)) task_patterns.push('даты ввода');
if(/таблиц|query|дебит|истори|управлен/.test(low)) task_patterns.push('извлечь таблицу');
if(/уточн|ambigu|clarif/.test(low)) task_patterns.push('clarification_needed');
""",
    planner_extra_js="files:Array.isArray(opened.files)?opened.files:[]",
    result_tools=["extract_", "ask_engineer"],
    texts=FallbackTexts(
        no_result_question=NO_EXTRACT,
        repeated_question=(
            "Excel Extractor несколько раз просматривал книгу, но не смог выбрать таблицу и колонки. "
            "Уточните, на каком листе и в каких колонках находятся скважины и нужные значения (даты ввода, режимы, дебиты)."
        ),
        done_message="Excel Extractor извлёк данные из книги.",
        no_result_issue="no_extract",
        question_id="Q-clarify",
        accepts_files=["xlsx"],
        missing_input_message="Нет Excel-файла для извлечения",
        missing_input_issue="missing_excel",
        missing_input_question="К задаче не приложен Excel-файл с данными. Приложите книгу .xlsx, из которой нужно взять скважины и даты.",
        no_result_failed_message="Excel Extractor не вернул факты",
        no_result_failed_issue="excel_agent_no_result",
    ),
    accepted_message="Excel Extractor принял задачу и анализирует workbook",
    progress_message="Excel Extractor читает листы и таблицы приложенных книг.",
    service_credentials=EXCEL_KEY_CRED,
    sticky_height=380,
    node_id_namespace="mas-excel-agent",
    workflow_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "mas-excel-extractor-agent")),
    example_objective="Достань даты ввода скважин",
    tool_grid_columns=4,
)
