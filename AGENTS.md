# AGENTS.md — как работать в этом репозитории

Точка входа для любого агента (Cursor, Claude, GPT) и человека. Читать первым. Детали — по ссылкам, не копировать сюда.

## 1. Что это

**NOVATEK RE MASter** — мультиагентная система (MAS), которая по задаче инженера-гидродинамика собирает или правит файл `SCHEDULE` (`.INC`) для tNavigator/ECLIPSE: читает Excel с датами ввода и параметрами скважин, задаёт инженеру вопросы по-русски, детерминированно применяет изменения к baseline и отдаёт новый `.INC`.

Стек: **n8n 2.30.8** (оркестратор + LLM-агенты, Qwen через OpenAI-compatible), **Postgres/PGVector** (кейсы, события, RAG), **FastAPI** сервисы на Windows (Activity `:8200`, Excel Tools `:8000`, Schedule Builder `:8090`, Math `:8100`). Целевая архитектура и долг — `MAS_REFACTORING_PLAN.md` (§3 цель, §2 долг, «Ближайшие шаги» — очередь работ). Полевой runbook — `docs.md`.

## 2. Карта репозитория

| Путь | Что | Проверка |
|---|---|---|
| `n8n/templates/generate_*.py` | **Единственный источник** workflow JSON: оркестратор, агенты, RAG, Runtime Config, Health Check | `node n8n/tests/*-smoke.js` |
| `n8n/templates/mas_state_utils.py` | JS-хелперы оркестратора (state, artifacts, ledger, HITL) — инлайнятся во все Code-ноды, должны быть идентичны | `mas-orchestrator-smoke.js` |
| `n8n/templates/mas_tool_nodes.py` | `tool_http(...)` — единственный способ дать LLM-агенту инструмент (`httpRequestTool` + `$fromAI`) | `test_workflow_contracts.py` |
| `n8n/workflows/core/*.json` | Сгенерированные workflows (импорт в n8n через UI). **Руками не править** | `scripts/mas_gate.py` (regen drift) |
| `n8n/rag/*.documents.json`, `n8n/templates/schedule_rag_workflows.py` | Карточки знаний (RAG) и allowlist ключевых слов | правило `schedule-keyword-allowlist` |
| `mas-activity-service/` | Activity: UI ленты, `POST /cases`, HITL `/answer`, артефакты; ходит в Postgres **только** через webhook `MAS — Control Plane Proxy` | `.venv/bin/python -m pytest` |
| `excel-agent-tools/` | Excel Tools: сессии над книгами, инвентарь, `extract_commissioning`, `extract_well_parameters`, `ask_engineer` | pytest (venv Activity), `excel-extractor-agent-smoke.js` |
| `schedule-builder-service/` | Schedule Builder: parse/apply/emit SCHEDULE, commissioning, group rebind, схемы keyword | pytest, `schedule-builder-agent-smoke.js` |
| `fastapi-math-service/` | Math: HTTP-агент `/agent/run` (без n8n workflow) | pytest |
| `simulation-model-example/` | Golden/combat кейсы и живой харнесс `run_live_five.py` (6 задач через Activity) | `scripts/mas_gate.py --live` |
| `scripts/` | Лабораторные инструменты: `mas_gate.py` (гейт), `mas_trace_case.py` (трасса кейса), `lab_soft_redeploy.py` (переимпорт в lab n8n) | — |
| `.cursor/rules/`, `.cursor/skills/` | Инварианты (всегда) и рецепты (`/mas-gate`, `/mas-trace`, `/mas-brief`) | — |
| `n8n/templates/retired/`, `n8n/workflows/retired/` | Старый контур. Не расширять, не импортировать | — |

## 3. Инварианты (нарушение = откат)

1. **Поле — UI-only n8n 2.30.8 и Windows с pip.** Никаких Node/Docker/`$env`/community-нод в runtime. Адреса — только `MAS — Runtime Config` и Credentials. (`.cursor/rules/field-deployment-constraints.mdc`)
2. **Оркестратор не знает домена.** Ни имён агентов, ни «типичных маршрутов», ни keyword-списков в промпте. Возможности — из `agent_registry`, политика — из RAG `orchestrator_routing`. (`mas-llm-first-no-domain-hardcode.mdc`)
3. **Regex — не замена решению LLM.** Выбор инструмента, таблицы, колонки, интерпретация ответа инженера — LLM (Agent tools / Structured Output). Regex допустим только как *гейт* (проверка «машинности» текста, лимиты, валидация аргументов).
4. **Детерминированное остаётся детерминированным.** Parse/apply/emit SCHEDULE, рендер keyword по схемам, INCLUDE-безопасность, DDL, лимиты шагов — Python/JS без LLM.
5. **HITL — разговор.** Вопрос инженеру — русская проза + `options[{value,label}]`; инженер даёт факты/таблицы/файлы, никогда не пишет `.INC`-строки и JSON. Тексты для человека без snake_case, `key=value`, JSON, `a|b`.
6. **Один контракт агента:** `agent_task → agent_result {status: completed|needs_input|failed, message, data, artifacts, issues, assumptions, requests[]}`. Агенты не вызывают друг друга.
7. **Ошибка аргументов инструмента — для LLM, не для инженера.** `spec_incomplete`, `column_not_found`, `question_not_human`, … возвращаются модели с подсказкой (`available_*`, `where_to_find`); человека спрашивают только через `ask_engineer`.
8. **Итоги — факты.** «Сдвинул даты 4 скважин: …», «Даты ввода: 14 скважин (…)», а не «вызвал 3 tools».
9. **Завершение — доказанное.** `finish` проходит `Verify completion` по журналу; лимит шагов — предохранитель, не способ завершить.
10. **Генерируем, не редактируем.** Меняется шаблон в `n8n/templates/`, потом регенерация. JSON руками — никогда.
11. **Секреты** — только Credentials n8n / `.env` сервисов.

## 4. Контракты (где смотреть)

- `agent_task`: `generate_mas_orchestrator.py` (`agentTask=…`): `case_id, task_id, agent_id, objective, handoff_message, inputs{activity_base_url, schedule_root, artifact_ids, data_refs, unlisted_wells_policy?, rework_reason?}, context{hitl{answers}}`.
- `agent_result` → сливается в `state.data.<agent>` (`Merge agent result`), `requests[]` → HITL, `message` → лента.
- Excel Tools: `POST /agent-tools/open_session` → `{session_id, inspect{files,sheets,tables[{table_id,file,sheet,columns,sample}]}, engineer_answers, rework_reason}`; инструменты — `POST /agent-tools/{name}` `{session_id, …args}`; результат — `GET /sessions/{id}/result`.
- Schedule Builder: `open_session` → `apply_commissioning` / `apply_group_rebind(spec)` / `apply_operations` / `render_ir` / `build_schedule` / `ask_engineer`; факты новых скважин — `_new_well_defs` (ответ инженера > `data.excel.new_wells` > `inputs`).
- Activity: `POST /cases` (multipart `file`, `schedule_files`, `attachments`), `GET /cases/{id}/events|state`, `POST /cases/{id}/answer` (файлы + сырой `{choice,text,label}` → оркестратор `resume` `source=human`, state HITL не пишет), `POST /cases/{id}/run` `action=resume` `source=agent|system`, `GET /cases/{id}/artifacts/{artifact_id}`; артефакты `excel`, `excel_1…`, `schedule_source[_N]`, `schedule_out`, `diff`.
- События: `case.created, agent.handoff, agent.accepted, agent.progress, agent.result, hitl.request, hitl.answered, orchestrator.decision, case.finished`; ошибки нод n8n — `Error — MAS Node Traces`.

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
| Новый инструмент LLM-агента | Python: функция с `@tool(_schema(...))` (Excel) или маршрут `/agent-tools/<name>` (Schedule); ошибки аргументов → `ToolError(code, hint, details)`; n8n: строка в `TOOLS` генератора → `tool_http(...)`; `SYSTEM` «когда вызывать»; smoke + pytest | `generate_excel_extractor_agent.py`, `mas_tool_nodes.py`, `.cursor/rules/excel-agent-tools.mdc` |
| Новый агент | Workflow по образцу Excel/Schedule (Normalize → open_session → AI Agent + tools → Summarize → Fetch result), строка в `agent_registry`, карточка RAG `orchestrator_routing`; до Фазы 2 — правка оркестратора (перечислить в `docs.md` §6) | `docs.md` §6 |
| Новое ключевое слово SCHEDULE | Проверить в мануале → allowlist `schedule_rag_workflows.KEYWORDS` → регенерация → карточка `keyword_instruction` → `docs.md` §3.2 | `.cursor/rules/schedule-keyword-allowlist.mdc` |
| Новый вопрос инженеру | Из инструмента агента через `ask_engineer` (проза + варианты «как их видит инженер»); из оркестратора — `ask_user` по схеме. Никогда — форма/enum | `human_text_problems` в обоих сервисах |
| Новое поведение оркестратора | Только общее (лимиты, верификация, слияние результата) — `generate_mas_orchestrator.py` + `mas-orchestrator-smoke.js`. Доменное знание — в агент или RAG | `MAS_REFACTORING_PLAN.md` §3 |
| Новый live-кейс | `simulation-model-example/combat-dates-revise/run_cases_local.py` (фикстуры) + `specs()` в `run_live_five.py`; сравнение `.INC` семантическое (`compare_schedules`) | `docs.md` §5 |

## 7. Ловушки, стоившие дня работы

- **`@n8n/n8n-nodes-langchain.toolHttpRequest` не исполняется в 2.30.8** (только `supplyData`). Инструменты — только `n8n-nodes-base.httpRequestTool` 4.4 через `tool_http`. Опциональный `json`-аргумент `$fromAI` не принимает пустоту — объявлять `string` с `""`, Python парсит JSON-строку.
- **Regex-роутер маскировал мёртвый LLM-путь** (`handoff_message` всегда содержал «даты ввода»). Любой «умный» regex в маршрутизации — долг; убирать, не расширять.
- **Qwen ставит флаги «на всякий случай»** (`goal_satisfied:true` при делегировании, `all_covered:false` при всех `covered:true`). Верить действиям и структуре, не флагам; выводить вердикт детерминированно из частей.
- **`nest_artifacts` терял второй `.xlsx`** и в Python, и в JS-копии. Хелперы состояния живут в двух языках — править оба и покрывать round-trip тестом.
- **Оркестратор удаляет `inputs.artifacts`** из `agent_task` — агенты берут карточки из `GET /cases/{id}/state`, а не из задачи. `action.task` от Decision LLM в `inputs` тоже не копируется: это парафраз, не факты (`CASE-6a9ec6b3-74e34e` — выдуманные даты в `task.facts` перекрыли `data.excel.facts`).
- **`_MACHINE_TOKEN_RE`** (`[a-z]+_[a-z_]+`, `key=value`, `{}[]`, `a|b`) применяется к любому тексту для инженера, включая имена файлов и листов — не вставлять их в `message`, класть в `data`.
- **Excel pytest** запускать из venv Activity: `PYTHONPATH=excel-agent-tools mas-activity-service/.venv/bin/python -m pytest excel-agent-tools/tests`. `run_live_five.py` — тем же venv (нужен `httpx`).
- **Полевые Docker-имена** (`excel-tools`, `mas-activity`, `n8n:5678`) не должны попадать в runtime-логику — только `MAS — Runtime Config`.
- **Порт Excel — `:8000`**, не 18000. Compose-сервисы bind-mount'ят код: после правок Python — `docker compose restart <svc>`, не rebuild.
- **`.INC` сравнивается семантически** (keyword на дате + канонизированные записи), не побайтно — решение заказчика; не возвращать побайтные диффы как дефект.

## 8. Делегирование более дешёвым моделям

Работает при трёх условиях: задача **одного пункта плана**, **гейт зелёный до и после**, **доказательство — трасса кейса**, не рассказ.

Как это устроено в Cursor:
- Этот файл и `alwaysApply`-правила (`mas-working-contract`, `field-deployment-constraints`, `mas-llm-first-no-domain-hardcode`, `schedule-*`) попадают в каждый чат сами. Правила по областям (`excel-agent-tools`, `schedule-builder-service`, `n8n-templates`, `mas-activity-service`, `live-harness`) подключаются, когда агент открывает файлы этой области — поэтому в брифе всегда называть конкретные файлы.
- Скиллы вызываются явно: `/mas-gate` (прогнать и прочитать гейт), `/mas-trace CASE-…` (разобрать кейс), `/mas-brief` (составить бриф для следующего агента).
- Один пункт плана = один новый чат. Бриф по шаблону `/mas-brief` (цель, границы, файлы, критерий готовности, команды, запреты) лежит в `briefs/<дата>-<пункт>.md`; первое сообщение нового чата — «Выполни бриф `briefs/…`». Последнее сообщение агента должно содержать таблицу `mas_gate.py` и id live-кейсов — иначе работа не принята.
- Сильная модель нужна там, где ещё нет трассы с названным решающим узлом: поведение Qwen от формулировки промпта, платформенные квирки n8n, двойники состояния в JS/Python. Остальное (инструмент, guard, текст итога, фикстура, smoke, карточка RAG, рецепт из §6) — работа для более дешёвой модели с брифом.
- Эскалация: дешёвая модель не «пробует ещё раз» после второго красного live-прогона — она оставляет трассу (`mas_trace_case.py … > /tmp/…`), гипотезу и останавливается.
