# AGENTS.md — как работать в этом репозитории

Точка входа для любого агента (Cursor, Claude, GPT) и человека. Читать первым. Детали — по ссылкам, не копировать сюда.

## 1. Что это

**NOVATEK RE MASter** — мультиагентная система (MAS), которая по задаче инженера-гидродинамика собирает или правит файл `SCHEDULE` (`.INC`) для tNavigator/ECLIPSE: читает Excel с датами ввода и параметрами скважин, задаёт инженеру вопросы по-русски, детерминированно применяет изменения к baseline и отдаёт новый `.INC`.

Стек: **n8n 2.30.8** (оркестратор + LLM-агенты, Qwen через OpenAI-compatible), **Postgres/PGVector** (кейсы, события, RAG), **FastAPI** сервисы на Windows (Activity `:8200`, Excel Tools `:8000`, Schedule Builder `:8090`, Math `:8100`). Целевая архитектура и долг — `MAS_REFACTORING_PLAN.md` (§3 цель, §2 долг, «Ближайшие шаги» — очередь работ). Полевой runbook — `docs.md`.

## 2. Карта репозитория

| Путь | Что | Проверка |
|---|---|---|
| `n8n/templates/generate_*.py` | **Единственный источник** workflow JSON: оркестратор, агенты, RAG, Runtime Config, Health Check | `node n8n/tests/*-smoke.js` |
| `n8n/templates/agents/*.py`, `mas_agent_spec.py`, `mas_agent_workflow.py` | **Спеки агентов** (`AgentSpec`): из одной спеки — workflow агента, поле URL в Runtime Config, строка seed реестра, проба Health Check | `demo-agent-smoke.js`, `test_workflow_contracts.py` |
| `mas-agent-kit/` | **Общее ядро агентов** — обычный модуль репозитория (не pip-пакет; `app/__init__.py` сервиса добавляет его в `sys.path`): `AgentService`, `SessionStore`, `ToolRegistry`/`ToolError`, `agent_result`/`in_progress`, `ask_engineer`, `human_text_problems`, `ActivityClient` (события, `upload`, `finish_task`), `CasePacket` | `pytest mas-agent-kit/tests` |
| `agents-template/demo_agent/` | **Шаблон нового агента** на kit'е + постоянный live-тест долгого агента (`in_progress` → `waiting_agent` → `resume source=agent`). README = рецепт. **Форма сервиса одна у всех агентов**: `app/agent.py` (`<Имя>Agent(AgentService)` + `agent = …()`), `app/agent_tools.py` (`@agent.tools.tool`), `app/main.py` (`create_agent_app` / `agent_router`) — проверяет `mas-agent-kit/tests/test_service_shape.py` | `pytest`, `run_live_demo_agent.py` |
| `n8n/templates/mas_state_utils.py` | JS-хелперы оркестратора (state, artifacts, ledger, HITL) — инлайнятся во все Code-ноды, должны быть идентичны | `mas-orchestrator-smoke.js` |
| `n8n/templates/mas_tool_nodes.py` | `tool_http(...)` — единственный способ дать LLM-агенту инструмент (`httpRequestTool` + `$fromAI`) | `test_workflow_contracts.py` |
| `n8n/workflows/core/*.json` | Сгенерированные workflows (импорт в n8n через UI). **Руками не править** | `scripts/mas_gate.py` (regen drift) |
| `n8n/rag/*.documents.json`, `n8n/templates/schedule_rag_workflows.py` | Карточки знаний (RAG) и allowlist ключевых слов | правило `schedule-keyword-allowlist` |
| `mas-activity-service/` | Activity: UI ленты и **Агенты** (`/registry`), `POST /cases`, HITL `/answer`, `POST /cases/{id}/artifacts` (deliverables агентов), `GET/PUT /agents`, **лог разработчика** `GET /cases/{id}/log` (`app/case_log.py`: `events` + `error_traces` → записи с `level`/`source`/шагами/ссылками на executions; UI — галочка «Режим разработчика» → вкладка «Лог», `static/log.js`); ходит в Postgres **только** через webhook `MAS — Control Plane Proxy` | `.venv/bin/python -m pytest` |
| `excel-agent-tools/` | Excel Tools (на kit'е): `app/agent.py` `ExcelExtractorAgent` — сессия над всеми книгами кейса, инвентарь, `normalize_args`, лента; `app/agent_tools.py` — `extract_commissioning`, `extract_well_parameters`, `ask_engineer`; `excel_tools.py` — детерминированный разбор книг; `legacy_api.py` — прямой `/api/v1` без n8n | pytest (venv Activity), `excel-extractor-agent-smoke.js` |
| `schedule-builder-service/` | Schedule Builder (на kit'е): `app/agent.py` `ScheduleBuilderAgent` — сессия над baseline, инвентарь, автосборка результата; `app/agent_tools.py` — инструменты (`apply_commissioning`, `apply_group_rebind`, …); parse/apply/emit SCHEDULE, commissioning, group rebind, схемы keyword — доменные модули | pytest, `schedule-builder-agent-smoke.js` |
| `fastapi-math-service/` | Math: HTTP-агент `/agent/run` (без n8n workflow) | pytest |
| `simulation-model-example/` | Golden/combat кейсы, живой харнесс `run_live_five.py` (6 задач через Activity) и `run_live_demo_agent.py` (агент-шаблон, долгая работа) | `scripts/mas_gate.py --live` |
| `scripts/` | Лабораторные инструменты: `mas_gate.py` (гейт), `mas_trace_case.py` (трасса кейса), `lab_soft_redeploy.py` (переимпорт в lab n8n) | — |
| `.cursor/rules/`, `.cursor/skills/` | Инварианты (всегда) и рецепты (`/mas-gate`, `/mas-trace`, `/mas-brief`) | — |
| `n8n/templates/retired/`, `n8n/workflows/retired/` | Старый контур. Не расширять, не импортировать | — |

## 3. Инварианты (нарушение = откат)

1. **Поле — UI-only n8n 2.30.8 и Windows с pip.** Никаких Node/Docker/`$env`/community-нод в runtime. Адреса — только `MAS — Runtime Config` и Credentials. (`.cursor/rules/field-deployment-constraints.mdc`)
2. **Оркестратор не знает домена.** Ни имён агентов, ни «типичных маршрутов», ни keyword-списков в промпте. Возможности — из `agent_registry`, политика — из RAG `orchestrator_routing`. (`mas-llm-first-no-domain-hardcode.mdc`)
3. **Regex — не замена решению LLM.** Выбор инструмента, таблицы, колонки, интерпретация ответа инженера — LLM (Agent tools / Structured Output). Regex допустим только как *гейт* (проверка «машинности» текста, лимиты, валидация аргументов).
4. **Детерминированное остаётся детерминированным.** Parse/apply/emit SCHEDULE, рендер keyword по схемам, INCLUDE-безопасность, DDL, лимиты шагов — Python/JS без LLM.
5. **HITL — разговор.** Вопрос инженеру — русская проза + `options[{value,label}]`; инженер даёт факты/таблицы/файлы, никогда не пишет `.INC`-строки и JSON. Тексты для человека без snake_case, `key=value`, JSON, `a|b`.
6. **Один контракт агента:** `agent_task → agent_result {status: completed|needs_input|in_progress|failed, message, data, artifacts, issues, assumptions, requests[], watch?}`. Агенты не вызывают друг друга. Долгий агент: `in_progress` сейчас → кейс `waiting_agent` → `ActivityClient.finish_task(agent_result)` (`POST /cases/{id}/run source=agent`) потом. Ядро — `mas-agent-kit`, не копии.
7. **Ошибка аргументов инструмента — для LLM, не для инженера.** `spec_incomplete`, `column_not_found`, `question_not_human`, … возвращаются модели с подсказкой (`available_*`, `where_to_find`); человека спрашивают только через `ask_engineer`.
8. **Итоги — факты.** «Сдвинул даты 4 скважин: …», «Даты ввода: 14 скважин (…)», а не «вызвал 3 tools».
9. **Завершение — доказанное.** `finish` проходит `Verify completion` по журналу; лимит шагов — предохранитель, не способ завершить.
10. **Генерируем, не редактируем.** Меняется шаблон в `n8n/templates/`, потом регенерация. JSON руками — никогда.
11. **Секреты** — только Credentials n8n / `.env` сервисов.

## 4. Контракты (где смотреть)

- `agent_task`: `generate_mas_orchestrator.py` (`agentTask=…`): `case_id, task_id, agent_id, objective, handoff_message, inputs{activity_base_url, schedule_root, artifact_ids, data_refs, unlisted_wells_policy?, rework_reason?}, context{hitl{answers}}`.
- `agent_result` → `state.agents[<agent_id>] = {status, summary, task_id, step, data, data_keys}` (`applyAgentResult` в `mas_state_utils.py` — один путь для `Merge agent result` и `resume source=agent`, без доменных bucket'ов), `requests[]` → HITL, `in_progress` → `waiting_agent`, `message` → лента. Кого вызвать и как — `agent_registry` (колонки — `mas_agent_registry.py`, seed — `agents/*.py` `AgentSpec.registry_row()`: `invoke`, `input_required`, `output_provides`, `input_schema`, `output_schema`, `hitl_policy`, `enabled`); привязка id после UI-импорта — `agent_workflow_ids` в Runtime Config, Activity → Агенты (`/registry`) или `PUT /agents/{agent_id}`.
- Excel Tools: `POST /agent-tools/open_session` → `{session_id, inspect{files,sheets,tables[{table_id,file,sheet,columns,sample}]}, engineer_answers, rework_reason}`; инструменты — `POST /agent-tools/{name}` `{session_id, …args}`; результат — `GET /sessions/{id}/result`.
- Schedule Builder: `open_session` → `apply_commissioning` / `apply_group_rebind(spec)` / `apply_operations` / `render_ir` / `build_schedule` / `ask_engineer`; факты новых скважин — `_new_well_defs` (ответ инженера > `new_wells` из `state.agents[*].data` > `inputs`).
- Activity: `POST /cases` (multipart `file`, `schedule_files`, `attachments`), `GET /cases/{id}/events|state`, `POST /cases/{id}/answer` (файлы + сырой `{choice,text,label}` → оркестратор `resume` `source=human`, state HITL не пишет), `POST /cases/{id}/run` `action=resume` `source=agent|system` (+ `agent_result` от долгого агента), `POST /cases/{id}/artifacts` (multipart от агента: `artifact_id`, `producer`, `summary` → карточка deliverable; kit `activity.upload`), `GET /cases/{id}/artifacts/{artifact_id}`, `GET/PUT /agents[/{agent_id}]`; артефакты `excel`, `excel_1…`, `schedule_source[_N]`, `schedule_out`, `diff`, `out_*`.
- События: `case.created, agent.handoff, agent.accepted, agent.progress, agent.result, hitl.request, hitl.answered, orchestrator.decision, case.finished`; ошибки нод n8n — `Error — MAS Node Traces` → `system.node_error` (+ `error_traces`). Только для лога разработчика (в чат не попадают): `trace.tool` — каждый вызов инструмента (пишет `agent_router` kit'а: `tool, ok, duration_ms, args, result|error`), `trace.note` — `ActivityClient.trace(title, level=…)`. Payload событий оркестратора/агентов несёт `execution_id`/`workflow_id` (`execRef()` в `mas_state_utils.py`, `exec_ref_js` в `mas_agent_workflow.py`) — из них Activity строит ссылку на execution (`N8N_PUBLIC_URL`). Большие значения в лог — только через `boundedForLog` (JS) / `compact_for_log` (kit).

## 5. Как здесь работают (цикл одной задачи)

1. **Взять задачу** из `MAS_REFACTORING_PLAN.md` → «Ближайшие шаги» (или из запроса пользователя). Один пункт — одна сессия. Границы — в самом пункте; не расширять.
2. **Базовая линия:** `python3 scripts/mas_gate.py` (offline, ~1–2 мин) до правок. Красный гейт до начала — сначала это.
3. **Понять, а не угадать:** живой дефект воспроизводится трассой — `python3 scripts/mas_trace_case.py CASE-… --n8n` (лента, state, аудит текста, n8n executions). Правка без воспроизведения = не принимается.
4. **Править источник:** шаблон/Python/тест. После шаблонов — регенерация (`cd n8n/templates && python3 generate_<x>.py`). Новые проверки — в pytest/smoke, регресс на каждый найденный дефект с id кейса в docstring.
5. **Гейт:** `python3 scripts/mas_gate.py` → зелёный; если тронуты оркестратор/агенты/emit — `--live` (6/6 `done`, `mismatch_count: 0`, без повторных HITL).
6. **Зафиксировать:** запись в `MAS_REFACTORING_PLAN.md` (таблица долга ✅/🟡 + «Ревизия N»: что нашли live, id кейсов, что сделали), правка `docs.md`, если изменился контракт/команда. Отчёт пользователю — фактами: файлы, счётчики, id кейсов, что не сделано и почему.

Ограничения по ходу: временные скрипты — в `/tmp`, не в репо; goldens `*_MAS_result.INC` не переписывать; `--wipe` кейсов и деструктивные действия — только по явной просьбе; вопросы пользователю — только когда ответ меняет работу (иначе принять допущение и назвать его).

## 6. Рецепты расширения

| Хочу | Делаю | Где описано |
|---|---|---|
| Новый инструмент LLM-агента | Python: `@agent.tools.tool(name, описание, properties)` в `app/agent_tools.py` → `(ctx, args) -> dict`; итог — `agent.store_result(ctx.state, agent.new_result(...))`; ошибки аргументов → `raise ToolError(code, hint, **details)`; спека: строка в `TOOLS` (`(name, описание, [(key, type, required, описание)])`); `system_prompt` «когда вызывать»; smoke + pytest | `agents-template/demo_agent/app/agent.py`, `schedule-builder-service/app/agent_tools.py`, `n8n/templates/agents/demo_agent.py` |
| Новый агент | Скопировать `agents-template/demo_agent/` (сервис на kit'е) и `agents/demo_agent.py` (`AgentSpec`) → `agents/__init__.py` `ALL` → `generate_<agent>.py` → регенерация Runtime Config / прокси / Health Check → smoke + pytest → на поле: импорт workflow, `<service_url_key>` в Runtime Config, включить в Activity → Агенты. Оркестратор не правится и не регенерируется (live `CASE-6a9f04a5-6f14b7`, `CASE-6a9f49ce-9cb933`) | `agents-template/demo_agent/README.md`, `docs.md` §6 |
| Долгий агент (часы) | Инструмент фиксирует `in_progress(...)` с `watch` и запускает фон; фон — `activity.progress(..., status="waiting_agent")`, в конце `activity.finish_task(result)`. Файлы-результаты — `activity.upload(...)` → карточка в `artifacts` | `start_long_job` в demo agent, `run_live_demo_agent.py` |
| Новое ключевое слово SCHEDULE | Проверить в мануале → allowlist `schedule_rag_workflows.KEYWORDS` → регенерация → карточка `keyword_instruction` → `docs.md` §3.2 | `.cursor/rules/schedule-keyword-allowlist.mdc` |
| Новый вопрос инженеру | Из инструмента агента через `ask_engineer` (проза + варианты «как их видит инженер»); из оркестратора — `ask_user` по схеме. Никогда — форма/enum | `human_text_problems` в обоих сервисах |
| Новое поведение оркестратора | Только общее (лимиты, верификация, слияние результата) — `generate_mas_orchestrator.py` + `mas-orchestrator-smoke.js`. Доменное знание — в агент или RAG | `MAS_REFACTORING_PLAN.md` §3 |
| Понять, что случилось в кейсе | Activity → «Режим разработчика» → «Лог» (или `GET /cases/{id}/log`): шаги, решения с `guard`, `agent_task`, `trace.tool` с аргументами LLM, ошибки узлов n8n со ссылкой на execution. Консоль (lab): `scripts/mas_trace_case.py` | `docs.md` §1.5, `/mas-trace` |
| Записать что-то для разработчика из агента | `agent.activity_for_state(state).trace("заголовок", level="warn", **детали)` → `trace.note`; вызовы инструментов kit логирует сам. Не писать техническое в `message`/`agent.progress` — это чат инженера | `mas_agent_kit/activity.py` |
| Новый live-кейс | `.INC`-кейс: `simulation-model-example/combat-dates-revise/run_cases_local.py` (фикстуры) + `specs()` в `run_live_five.py`, сравнение семантическое (`compare_schedules`). Кейс без файлов / другой агент: по образцу `run_live_demo_agent.py` (включить строку реестра, кейс, проверка `state.agents`, выключить) | `docs.md` §5 |

## 7. Ловушки, стоившие дня работы

- **`@n8n/n8n-nodes-langchain.toolHttpRequest` не исполняется в 2.30.8** (только `supplyData`). Инструменты — только `n8n-nodes-base.httpRequestTool` 4.4 через `tool_http`. Опциональный `json`-аргумент `$fromAI` не принимает пустоту — объявлять `string` с `""`, Python парсит JSON-строку.
- **Regex-роутер маскировал мёртвый LLM-путь** (`handoff_message` всегда содержал «даты ввода»). Любой «умный» regex в маршрутизации — долг; убирать, не расширять.
- **Qwen ставит флаги «на всякий случай»** (`goal_satisfied:true` при делегировании, `all_covered:false` при всех `covered:true`). Верить действиям и структуре, не флагам; выводить вердикт детерминированно из частей.
- **`nest_artifacts` терял второй `.xlsx`** и в Python, и в JS-копии. Хелперы состояния живут в двух языках — править оба и покрывать round-trip тестом.
- **Оркестратор удаляет `inputs.artifacts`** из `agent_task` — агенты берут карточки из `GET /cases/{id}/state`, а не из задачи. `action.task` от Decision LLM в `inputs` тоже не копируется: это парафраз, не факты (`CASE-6a9ec6b3-74e34e` — выдуманные даты в `task.facts` перекрыли `data.excel.facts`).
- **`_MACHINE_TOKEN_RE`** (`[a-z]+_[a-z_]+`, `key=value`, `{}[]`, `a|b`) применяется к любому тексту для инженера, включая имена файлов и листов — не вставлять их в `message`, класть в `data`.
- **Excel pytest** запускать из venv Activity: `PYTHONPATH=excel-agent-tools mas-activity-service/.venv/bin/python -m pytest excel-agent-tools/tests`. `run_live_five.py` — тем же venv (нужен `httpx`).
- **Полевые Docker-имена** (`excel-tools`, `mas-activity`, `n8n:5678`) не должны попадать в runtime-логику — только `MAS — Runtime Config`.
- **Порт Excel — `:8000`**, не 18000. Compose-сервисы bind-mount'ят код без `--reload`: после правок Python — `docker compose restart <svc>`, не rebuild. `lab_soft_redeploy.py` (и `mas_gate.py --live`) перезапускает `excel-tools`, `math-service`, `schedule-builder` сам — иначе live-гейт тестирует старый модуль (`CASE-6a9efd37-367ae1`: Builder 11 часов читал `data.excel` после переезда на `state.agents`).
- **`.INC` сравнивается семантически** (keyword на дате + канонизированные записи), не побайтно — решение заказчика; не возвращать побайтные диффы как дефект.
- **Сервис не поднялся — live-гейт показывает 6/6 одинаковый ложный HITL** («К задаче не приложен исходный SCHEDULE» от workflow-фолбэка), а не ошибку. Так `schedule-builder` падал, пока kit ставился pip'ом (гонка сборки wheel в общем bind-mount); теперь `mas_agent_kit` — обычный модуль, который `app/__init__.py` находит по `sys.path`, ставить нечего. Redeploy падает, если сервис не ответил на `/health`. Если все кейсы упали одинаково — сначала `docker compose ps -a` и логи сервиса, потом трасса.
- **`mas_agent_kit` не pip-пакет.** Не добавлять `pyproject.toml`/`../mas-agent-kit` в requirements: зависимости kit'а (`fastapi`, `filelock`) — в `requirements.txt` сервиса, импорт — через `app/__init__.py` (копировать из `agents-template/demo_agent`). Поле — `.venv` + `requirements.txt`, без poetry. Старая editable-установка kit'а в venv (`__editable__.mas_agent_kit…pth`) маскирует ошибки этого импорта — `cd /tmp && python -c "import mas_agent_kit"` из venv должен падать.
- **Форма сервиса агента одна** (`app/agent.py` класс + `agent = …()`, `app/agent_tools.py` инструменты, `app/main.py` только app): не писать свои `/agent-tools/*`-маршруты и модульные `open_session`/`_store_result` — `test_service_shape.py` в гейте покраснеет.
- **Новый статус кейса = правка CHECK в Postgres.** `waiting_agent` уронил `Update case after agent` на живой базе (`CASE-6a9f3a76-38506f`): `CREATE TABLE IF NOT EXISTS` не обновляет ограничение. Статусы — `CASE_STATUSES` в `generate_mas_control_plane_proxy.py` (двойник `contracts.CASE_STATUSES`), `schema` пересоздаёт `cases_status_check`.
- **Поля `agent_result` вне фиксированного набора теряются в `Format result`** (`watch` у `in_progress`, `CASE-6a9f3bc9-10cb9f`): новое поле контракта — добавить в `js_format_result` `mas_agent_workflow.py` и в `applyAgentResult`.
- **Лог разработчика — это те же `events`.** Новый вид события: добавить в `EVENT_KINDS` обоих мест (`contracts.py` Activity и `activity.py` kit'а); техническое — с префиксом `trace.` (Activity скрывает его из чата по `is_trace_kind`, `case_log.record_level/record_source/record_title` дают уровень, источник и заголовок). Ссылка на execution появляется только если payload несёт `execution_id` **и** `workflow_id` — новые Code-ноды, пишущие события, добавляют `...execRef()`. Старый кейс без ссылок — норма, не дефект.

## 8. Делегирование более дешёвым моделям

Работает при трёх условиях: задача **одного пункта плана**, **гейт зелёный до и после**, **доказательство — трасса кейса**, не рассказ.

Как это устроено в Cursor:
- Этот файл и `alwaysApply`-правила (`mas-working-contract`, `field-deployment-constraints`, `mas-llm-first-no-domain-hardcode`, `schedule-*`) попадают в каждый чат сами. Правила по областям (`excel-agent-tools`, `schedule-builder-service`, `n8n-templates`, `mas-activity-service`, `live-harness`) подключаются, когда агент открывает файлы этой области — поэтому в брифе всегда называть конкретные файлы.
- Скиллы вызываются явно: `/mas-gate` (прогнать и прочитать гейт), `/mas-trace CASE-…` (разобрать кейс), `/mas-brief` (составить бриф для следующего агента).
- Один пункт плана = один новый чат. Бриф по шаблону `/mas-brief` (цель, границы, файлы, критерий готовности, команды, запреты) лежит в `briefs/<дата>-<пункт>.md`; первое сообщение нового чата — «Выполни бриф `briefs/…`». Последнее сообщение агента должно содержать таблицу `mas_gate.py` и id live-кейсов — иначе работа не принята.
- Сильная модель нужна там, где ещё нет трассы с названным решающим узлом: поведение Qwen от формулировки промпта, платформенные квирки n8n, двойники состояния в JS/Python. Остальное (инструмент, guard, текст итога, фикстура, smoke, карточка RAG, рецепт из §6) — работа для более дешёвой модели с брифом.
- Эскалация: дешёвая модель не «пробует ещё раз» после второго красного live-прогона — она оставляет трассу (`mas_trace_case.py … > /tmp/…`), гипотезу и останавливается.
