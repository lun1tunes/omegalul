# NOVATEK RE MASter — анализ и план рефакторинга «от хардкода к инженерной MAS»

Ревизия 5 — 2026-09-07 (день). Статусы ниже сверены с кодом на эту дату; история ревизий — §7. Как работать по плану — `AGENTS.md` (карта, инварианты, цикл задачи), гейт — `python3 scripts/mas_gate.py [--live]`.

Ограничения, которые план не нарушает: n8n **2.30.8**, только UI (Import from File, Credentials, Set-ноды); FastAPI на Windows — только Python; вся правка адресов и лимитов — в `MAS — Runtime Config`; секретов в JSON нет.

Легенда статусов: ✅ сделано · 🟡 частично · ⬜ не начато · 🆕 найдено в live-прогонах (ревизии 2–4), а не в ревизии 1.

---

## 0. Диагноз (обновлён)

Архитектурный вектор верный: тонкий оркестратор «1 шаг = 1 execution», стейт в Postgres за прокси, агенты = LLM + FastAPI-инструменты, Activity как единый хаб, RAG с изоляцией по `target_base`, детерминированный emit SCHEDULE.

Ревизия 1 ставила диагноз «решения LLM принимают регулярки, HITL машинный». Это подтвердилось, но реальная работа показала **три вещи, которых в ревизии 1 не было**:

1. **У оркестратора не было памяти о сделанном.** Он видел флаги (`has_schedule_out: true`), но не результаты агентов. Пока в `planner_input` стоял rule-based hint, это маскировалось; стоило его убрать — контур зациклился (schedule_builder `completed` 23 раза подряд, до лимита 24 шагов). Завершение задачи — не «убрать хинт», а отдельная подсистема: журнал + критерий завершения + инварианты.
2. **Машинность живёт в инструментах, не только в промптах.** FastAPI-tools отдают `needs_input` вида `missing: [rate, wells]`, агент превращает это в «Уточните rate для перепривязки групп», оркестратор честно несёт человеку. Контракт уточнения нужен на уровне **каждого** инструмента.
3. **Qwen галлюцинирует даже с журналом перед глазами.** В `CASE-6a9dac9d` LLM написала «schedule_builder отработал обновление» и сделала `finish`, когда билдер только задал вопрос. Вывод: «оркестратор без домена» ≠ «промпт без правил». Нужен слой **доменно-нейтральных инвариантов** (ответ человека должен дойти до того, кто спросил; повтор без нового ввода — не исполнение, а review), и на него надо закладывать время.

---

## 1. Что сделано правильно (не трогать)

| Область | Почему правильно |
|---|---|
| Тонкий оркестратор, self-POST `action:step`, стейт в `cases/events` | Масштабируется, восстанавливается после падений, Activity не крутит цикл |
| Control Plane Proxy как единственный путь в Postgres | Полевой FastAPI без драйвера БД, один DDL |
| Детерминированный SCHEDULE-движок (`parse.py`, `apply.py`, `emit.py`, `timeline_ops.py`, `schema_renderer.py`, `schema_catalogues.json`) | Синтаксис `.INC`, терминаторы `/`, порядок keyword в DATES, INCLUDE-безопасность — не место для LLM |
| «Факт из Excel ≠ приказ; prose ≠ authority» для удаления скважин | Деструктив только по явному решению человека |
| RAG: одна таблица, `target_base` срезы, карточки как политика | Механизм расширяемости на месте, недоиспользован |
| Лента событий на русском | Читается как диалог; теперь и `case.finished` — итог по журналу, а не шаблон |
| 🆕 Журнал задачи (`state.ledger`) + `progress` в решении + предохранители в `Parse decision` + проверенное завершение (`Verify completion`) | Завершение — осознанное и подтверждённое решение по фактам, а цикл невозможен молча (`docs.md` §1.3) |
| 🆕 `run_live_five.py` как регресс-гейт | Кейс проваливается за повторный handoff без нового ввода, review-эскалацию, `step_count > 8`, статус `failed`, повтор того же вопроса после ответа, `done` без `.INC`, машинный текст инженеру; `.INC` сравнивается семантически |
| 🆕 Агенты LLM-first на `httpRequestTool` + `$fromAI` (`mas_tool_nodes.py`) | LLM выбирает инструмент и аргументы, Python исполняет детерминированно; ошибки аргументов возвращаются LLM (`spec_incomplete`, `column_not_found`…), человек — только через `ask_engineer` |
| 🆕 Слой знаний для агентов-исполнителей (`AGENTS.md`, `.cursor/rules`, `.cursor/skills`, `scripts/mas_gate.py`, `scripts/mas_trace_case.py`) | Один пункт плана можно отдать другой модели с брифом; приёмка — таблица гейта и id live-кейсов |

---

## 2. Инвентарь хардкода и «машинных директив» — со статусами

### 2.1 Оркестратор

| # | Где | Что | Статус |
|---|---|---|---|
| O1 | `SYSTEM` | Агенты поимённо; «типичный путь excel_extractor → schedule_builder»; legacy-маппинг `*_specialist` | ⬜ Правило «schedule_out есть — finish» заменено журналом. В промпте по-прежнему: список агентов с описаниями, два «Типичных пути», маппинг `excel_extraction_specialist→excel_extractor`, «не вызывай excel_extractor, если Excel нет» (Фаза 2) |
| O2 | `Prepare decision context` | Rule-based hint «следующий шаг» | ✅ Удалён; smoke проверяет отсутствие маршрутных подсказок в `planner_input` |
| O3 | `ROUTE`, `MERGE` | `if agent_id==='excel_extractor'…`; bucket `excel/calc/schedule` | ⬜ `MERGE`: `bucket=agentId==='excel_extractor'?'excel':…` и `slimExcel` для Excel; Schedule Builder читает `data.excel` (`excel_bucket`) — при переходе на `state.agents[<id>]` менять оба (Фаза 2) |
| O4 | `mas_state_utils.py` `inferRouting*` | Regex по goal для RAG-фильтров | ⬜ Фаза 2 |
| O5 | 8 routing-карточек RAG | Дублируют O1 | ⬜ 8 карточек `orchestrator_routing` в `n8n/rag` — переписать как политики (Фаза 2) |
| O6 | `DECISION_SCHEMA` | `options[]` терялись по дороге к человеку | 🟡 UI рендерит `options[{value,label}]` кнопками; `question_id` LLM-вопросов всё ещё свободный |
| O7 | `specialist_packet` и retired-контракты | Два контракта | ✅ Не принимаются; retired в `n8n/templates/retired`, `n8n/contracts/retired` |
| O8 | `MERGE` лимиты | `step_count >= 24` захардкожен, при достижении — тихий `failed` | ✅ `max_steps` в Runtime Config (12); при достижении с результатом — review-гейт с человеком, без результата — честный `failed` |
| 🆕 O9 | Весь оркестратор | **Нет памяти о результатах агентов**; `current_task` + флаги вместо истории → цикл `CASE-6a9da4e2` | ✅ Журнал `state.ledger.history` (агенты + человек), блок «Журнал задачи» в `planner_input`, `compact.journal` |
| 🆕 O10 | `DECISION_SCHEMA` / `Parse decision` | Нет критерия завершения: LLM выбирала `finish`/`call_agent` «по привычке» | ✅ Обязательный `progress {goal_satisfied, evidence, missing, is_repeating}`; `finish` при `goal_satisfied`, `summary_for_human` из summary агентов |
| 🆕 O11 | `Parse decision` | Ложный `finish` после ответа человека (`CASE-6a9dac9d`): агент спросил → человек ответил → LLM «завершила», не вернув ответ агенту | ✅ Инвариант `answer_not_applied`: ответ уходит агенту, который спросил; `finish` невозможен, пока он не вернул `completed` |
| 🆕 O12 | `Parse decision` | Повторное делегирование после `completed` исполнялось молча | ✅ Один повтор только с `rework_reason` (уходит агенту), дальше — review-гейт `result_approval`; ответ человека сбрасывает stall |
| 🆕 O13 | `Parse decision` `plan_update` | Мержится по `item.id`, а LLM пишет `{step, action, agent_id, reason}` → `plan` всегда `[]`, декомпозиция не сохраняется | ⬜ Фаза 2.5: схема `plan_update` с обязательным `id`, показ в Activity |
| 🆕 O14 | `Parse decision` guard `goal_satisfied` | Ложный `finish` без Schedule Builder (`CASE-6a9dc4b3`): LLM выбрала верное `call_agent`, но ошибочно поставила `goal_satisfied:true`; guard доверил флагу, а не действию | ✅ Guard срабатывает только при **повторном** делегировании агенту с `completed` (привычка); первое делегирование — действие побеждает, флаг игнорируется (`guard: goal_flag_ignored` в журнале). Harness падает сразу на `done` без `.INC` |
| 🆕 O16 | Decision LLM `finish` без проверки | Ложный `finish` после одного Excel (`CASE-6a9e46ea`, `CASE-6a9e46fc`): LLM сама выбрала `finish` и написала «schedule обновлён», хотя Schedule Builder не вызывался; ни один детерминированный guard это не ловит (и не должен — это семантика) | ✅ **Проверенное завершение**: второй LLM-проход `Verify completion` (цель → части-результаты → покрытие записями журнала `completed`, `unsupported_claims`); отказ → `continue`-шаг с пробелом в журнале, повторный отказ → review-гейт прозой; `completion_verified` в `case.finished` |
| 🆕 A13 | `agent_tools._new_well_defs` | Decision LLM эхом положила в `task.new_wells` голый список имён (`["N001",…]`); lookup брал непустой `inputs.new_wells`, фильтровал до пустоты и **не смотрел** ответ инженера → тот же вопрос про новые скважины дважды (`CASE-6a9e4c07`) | ✅ Факты инженера из HITL — первыми; из `inputs` принимаются только строки-определения (dict с `well`); pytest-регресс |
| 🆕 O15 | `Parse decision` `finish` | `summary_for_human` от LLM с `schedule_builder`/`schedule_out` — инженер читал идентификаторы | ✅ Правило в промпте (title из реестра, «новый schedule.inc») + детерминированный fallback на итог по журналу, если текст «машинный» (`looksMachineText`) |

### 2.2 HITL

| # | Где | Что | Статус |
|---|---|---|---|
| H1 | `timeline_ops.py` unlisted | `expected_format: "keep\|remove"`, `enum` | ✅ Проза + `options[{value,label,hint}]`, `accepts.free_text` |
| H2 | `parseKeepRemove` и зеркало в Activity | Regex по ответу человека | 🟡 Кнопка даёт `choice` — regex не нужен; `parseKeepRemove` (`mas_state_utils.py`) и `parse_keep_remove` (`state_shape.py`) остались как fallback для свободного текста до 1.3 |
| H3 | `new_well_defs` со строками `.inc` | Человек пишет SCHEDULE руками | ✅ Инженерные факты `new_wells` (группа, MD верх/низ, диаметр, режим+дебит, BHP, VFP, файл WELLTRACK); `compose_new_well_lines` собирает `WELSPECS/COMPDATMD/WCONPROD` по мануалу; недостающий факт = finding. Legacy typed-lines принимается для совместимости |
| H4 | combat-3: вопрос про новые скважины не задавался | Диалог не соответствовал уточнениям | ✅ Два последовательных вопроса (unlisted → new_wells), harness отвечает на каждый отдельно |
| H5 | `hitl_user_copy.py` + `humanizeQuestion` | Два слоя «компиляции» кодов в русский | ✅ Закрыт решением: в MAS-контуре композитора нет — инструменты отдают прозу (H9), `ask_user` оркестратора ограничен схемой. `hitl_user_copy.py` используется только schedule-intake контуром (`schedule_pipeline.py`, `schedule_intake_runtime.py`) как словарь кодов |
| H6 | Activity `/answer` пишет в state напрямую, оркестратор — через `resume` | Два write-path | 🟡 Журнал подхватывает ответы из `hitl.answers` независимо от пути (`reconcileLedgerAnswers`); сами два пути остались (Фаза 1.3) |
| H7 | `app.js renderGate` | Без кнопок, `kind` всегда `needs_input` | ✅ Кнопки, `choice`+`label`, эхо «Вы решили: …», `kind` из вопроса (`result_approval` для review), без `expected_version`/`gate_id` в DOM; подсказка колонок таблицы для `accepts.table` |
| H8 | `agent.result` шаблон `DESCRIBE_APPLY` | Лента врала про сделанное | ✅ `summarize_commissioning_result` по фактическому diff (сдвинуто/добавлено/убрано/оставлено); `agent.result` эмитит только оркестратор |
| 🆕 H9 | `agent_tools.py` `apply_group_rebind` (и `main.py`) | `needs_input` с `requests=[{question:"Уточните {item} для перепривязки групп"}]` по `missing` → человек читает «Уточните rate» (`CASE-6a9dafa5`, два раунда) | ✅ Неполный spec → `ok:false, error:spec_incomplete {missing, where_to_find}` **для LLM**; вопрос человеку — только `ask_engineer` (проза, `options`, `accepts_files`), машинный текст отклоняется `question_not_human`. `human_text_problems` — общий фильтр; `run_live_five` проверяет каждый HITL/итог на «машинность» |
| 🆕 H10 | Activity `case.finished` | Текст «Задача завершена. Загрузите результаты работы.» затирал итог оркестратора | ✅ Показывается `status_message` оркестратора (итог по журналу), шаблон — fallback |

### 2.3 Агенты и FastAPI

| # | Где | Что | Статус |
|---|---|---|---|
| A1 | Excel `_COMMISSIONING_RE` → HTTP без LLM | LLM-substitute | ✅ Регекс, `suggested_capability`, Capability router и HTTP-обход `extract_commissioning` удалены; LLM выбирает таблицу/колонки по инвентарю `open_session` |
| A2 | `WELL_COL`/`DATE_COL` substring | Выбор колонок без подтверждения LLM | ✅ `extract_commissioning(table_id, well_column, date_column)`, `extract_well_parameters(table_id, well_column, mapping)`; несуществующая/не-датовая колонка → `column_not_found`/`column_not_dates` LLM с `available_columns` |
| A3 | Schedule `suggested_capability` | Regex `GROUP_INTENT`, «есть facts → commissioning» | ✅ Capability router и HTTP-обход `apply_*` удалены; инструмент выбирает LLM |
| A4 | `group_rebind.py` `extract_group_rebind_spec`, `_parse_rate`, `G{well}` | Регулярки вместо структурированного вывода | ✅ `normalize_group_rebind_spec` от структуры LLM; неполный spec → `spec_incomplete` LLM, не человеку |
| A5 | Fallback `["GNEW","GINJ","GPROD"]`, «Уточните {item}» | Заглушки вместо GRUPTREE | ✅ `gruptree_summary` + `ask_engineer` прозой с вариантами из baseline |
| A6 | `INTENT_ALIASES` | Словарь keyword | ⬜ `keywords.py` → `search_keywords`/`GET /keywords/search`: подсказка поиска для LLM, не выбор инструмента. Заменить на выборку RAG `keyword_instruction` (Фаза 3.6, низкий приоритет) |
| A7 | `SUMMARIZE_AI`/`DESCRIBE_*` | Машинные итоги | ✅ Schedule Builder — сдвиги/добавления/удаления по скважинам; Excel — «Даты ввода: N скважин (…), с … по …» / «Параметры новых скважин: N (…) — группа, интервал MD, …»; `DESCRIBE_EXTRACT` удалён |
| A8 | Python `/agent/run` дубли | Второй источник поведения | ✅ Schedule и Excel `/agent/run` удалены; остался только Math (HTTP-агент без n8n-workflow) |
| A9 | Schedule Builder LLM на commissioning-задаче вызвала `apply_group_rebind` (`CASE-6a9dafa5`) | Промпт агента/описания инструментов не удерживают LLM от «попробовать всё» | ✅ Новый `SYSTEM` «какой инструмент когда», `apply_group_rebind` требует полный spec; live 6/6 без лишних apply |
| A10 | `group_rebind_revise` | Порядок скважин в записях зависел от порядка в spec от LLM (golden 2 мигал: 1602 перед 1601) | ✅ Детерминированный порядок по первому появлению в baseline |
| 🆕 A11 | **`@n8n/n8n-nodes-langchain.toolHttpRequest` в n8n 2.30.8** | Узел скрыт (`hidden: true`) и имеет только `supplyData`; AI Agent v3 исполняет инструменты через движок → каждый вызов падал «has a supplyData method but no execute method». LLM-путь обоих агентов **никогда не работал** в 2.30.8 — это маскировал regex-роутер с прямыми HTTP | ✅ Оба агента на `n8n-nodes-base.httpRequestTool` 4.4 + `$fromAI(...)` (`mas_tool_nodes.py`); реестр версий в `test_workflow_contracts.py` |
| 🆕 A12 | `_remove_unlisted_commissioning` | Политика «убрать скважины вне Excel» удаляла и wildcard-записи (`WEFAC '*' 0.95`, `WTEST * 30 P`) — молча меняла uptime всей модели (`CASE-6a9dc8b7`) | ✅ `_is_named_well` исключает `*`/`?`-паттерны; remove трогает только именованные скважины; pytest-регресс |

### 2.4 Расширяемость и гигиена

- Добавить агента сегодня = правки `SYSTEM`, `ROUTE`, узел вызова, `MERGE` bucket, sticky, RAG-карточка, SQL seed, agent workflow — ⬜ Фаза 2/4 (рецепт — `AGENTS.md` §6, `docs.md` §6).
- Добавить инструмент агенту = Python-функция/ветка + строка `tool_http(...)` в генераторе + `SYSTEM` + smoke + pytest — ✅ рецепт зафиксирован (`.cursor/rules/excel-agent-tools.mdc`, `schedule-builder-service.mdc`).
- `KEYWORDS` один источник — ✅. Retired-контур вне live-генерации/smokes — ✅. Legacy Activity `/v1/tasks*` — 🟡 задокументирован в `main.py`, вынос отложен (связность тестов).
- Генерация workflow — фиксированная точка: `scripts/mas_gate.py --only regen` (порядок генераторов, relayout последним, `versionId` игнорируется) — ✅.

### 2.5 Результат кейса как один файл (найдено в ревизии 5)

| # | Где | Что | Статус |
|---|---|---|---|
| 🆕 R1 | `state_shape.py` `nest_artifacts`/`role_for_artifact_id` и JS-двойник `mas_state_utils.py` (`nestArtifacts`, `roleForArtifactId`, `mergeIncomingArtifacts`) | Фиксированные слоты `excel/schedule_source/surface/trajectory/schedule_out/diff`; роль угадывается по имени id; новый вид артефакта = правка обоих двойников (дважды теряли файлы: `excel_1`) | ✅ карточки `{artifact_id, role, kind, producer, filename, bytes, summary, created_at}` в обоих двойниках; `nest/flatten` round-trip без потерь (pytest + smoke); `producer`/`kind` ставит `mergeIncomingArtifacts(..., agentId)`; загрузки инженера — `kind=input, producer=user` в Activity |
| 🆕 R2 | Activity `GET /cases/{id}/schedule`, `_result_filename`, `_schedule_out_text`, `schedule_artifact` в snapshot | Специальная ручка и поле «результат = schedule_out» | ✅ `GET /cases/{id}/artifacts?kind=&producer=`, `GET /agents`; snapshot/SSE отдают `artifacts[]` + `deliverables[]`, `schedule_artifact` удалён из live API (остался в legacy `/v1/tasks*`); `/schedule` — алиас; inline-артефакты (`schedule_out`, `diff`) отдаются из state без blob store |
| 🆕 R3 | UI `index.html`/`app.js`: `scheduleDownloadHead` «Скачать schedule.inc», `schemaEndLabel` «Результат», `setScheduleArtifact` | Один файл подаётся как итог всей работы | ✅ UI переписан (`index.html`, `app.css`, `app.js`, `schema.js`, `knowledge.*`): чипы deliverables под `agent.result` и под итогом, панель «Результаты» по агентам (подписи из `GET /agents`), «Исходные данные» — карточки `kind=input`; ни одного имени агента в UI-коде; схема строит узлы агентов из реестра и событий |
| 🆕 O17 | `Prepare decision context` `has_schedule_out` (`hasScheduleOut`) | Доменный флаг в `planner_input` — та же утечка, что O1 | ✅ `planner_input.deliverables` + `case.finished.payload.deliverables`; `grep has_schedule_out` по коду пуст |
| 🆕 R4 | `run_live_five.py`: «`done` без `schedule_out` = провал», скачивание через `/schedule` | Ожидание `.INC` зашито в харнесс | ✅ `expects` в `specs()`; «`done` без ожидаемых deliverables = провал», у каждого deliverable есть `producer`; скачивание через `GET /cases/{id}/artifacts` → `download_path` |

---

## 3. Целевая архитектура (принципы, дополнены)

1. **Оркестратор не знает домена.** Промпт — роль, формат решения, правила безопасности. Доменное — из `agent_registry` и RAG `orchestrator_routing` (политика, не маршрут).
2. **Реестр исполняемый.** `invoke`, `input_schema`, `output_schema`, `hitl_policy` в строке реестра; универсальный узел вызова; новый агент без правки оркестратора.
3. **Агент = LLM с инструментами, инструменты детерминированы.** Решение «какой инструмент и с чем» — LLM агента через Structured Output; regex-роутеры и `_parse_*` уходят.
4. **HITL — разговор инженеров.** Вопрос — проза + `options[{value,label,hint}]` + `accepts{free_text, files, table}`; кнопки в UI; ответ — `choice` + свободный текст; инженер даёт факты/таблицы/файлы, не `.INC`. **Дополнение:** это контракт **каждого инструмента**, не только агента: `needs_input` без прозы и вариантов — дефект.
5. **Один контракт агента.** `agent_task` → `agent_result {task_id, agent_id, status: completed|needs_input|failed, message, data, artifacts, issues, assumptions, requests[]}`; `summary_for_human` — поле итога оркестратора в `finish`, не агента.
6. **Лента — источник правды.** `summary_for_human` по фактическому diff; `case.finished` — итог по журналу.
7. 🆕 **Оркестратор помнит и проверяет.** Журнал (кто что сделал, что ответил человек) — единственная основа для решения о завершении. Поверх LLM — доменно-нейтральные инварианты: ответ человека доходит до спросившего; повтор без нового ввода — review, не исполнение; флагам LLM (`goal_satisfied`, `all_covered`) не верят — верят действию и структуре (`goal_parts`); `finish` проходит второй скептический проход `Verify completion` по журналу (отказ → ещё шаг, второй отказ → инженеру); бюджет шагов — эскалация к человеку, не тихий `failed`. Каждый инвариант — с `guard` в `orchestrator.decision.payload` для аудита, `completion_verified` — в `case.finished`.
8. 🆕 **Live-гейт после каждой правки оркестратора/агентов.** Smokes проверяют структуру, но цикл и галлюцинацию ловит только живой прогон с проверкой «завершение осознанное»: `run_live_five.py` обязателен перед «готово».
9. 🆕 **Результат кейса — набор deliverables от агентов, не один файл.** SCHEDULE `.INC` — промежуточный этап более длинной цепи (файлы модели на кластере → запуск tNavigator через CLI → мониторинг расчёта → выгрузка из бинарных результатов). Артефакт — карточка `{artifact_id, role, kind: input|intermediate|deliverable, producer: user|<agent_id>, filename, mime, bytes, summary}`; UI показывает результаты у каждого `agent.result` и в панели «Результаты» по агентам; `case.finished` — итог по агентам со списком deliverables. Ни UI, ни Activity, ни оркестратор не знают слова `schedule_out` как «итог всей работы» (сделано в Фазе 1.5).
10. 🆕 **Долгие агенты.** Расчёт идёт часами при модели «1 шаг = 1 execution»: агенту нужен статус `in_progress` с дескриптором наблюдения; монитор шлёт `agent.progress`, а завершение будит оркестратор тем же `resume`, что и ответ инженера — источник «внешнее событие», не человек. Закладывается в Фазе 1.3 (единый write-path), реализуется с первым долгим агентом.

---

## 4. План по фазам (ревизия 5)

Гейт каждой фазы — `python3 scripts/mas_gate.py`: регенерация без дрейфа, 13 smokes, pytest (Activity 150, Schedule 57, Excel 87), offline combat; `--live` — `run_live_five.py` 6/6 `done`, `mismatch_count: 0`, без циклов/повторных HITL/review. Порядок фаз: сначала то, что инженер видит в ленте, затем оркестратор без домена, затем расширяемость как продукт.

### Фаза 0 — Гигиена ✅

Сделано: retired-контур вынесен (`n8n/templates/retired`, `n8n/tests/retired`, `n8n/contracts/retired`), один `KEYWORDS`, `specialist_packet` не принимается, правило `mas-llm-first-no-domain-hardcode.mdc`, Health Check берёт URL из Runtime Config. Хвост: legacy Activity API `/v1/tasks*` — вынос при удобном случае.

### Фаза 1 — Человеческий HITL 🟡 (осталось: 1.3 и структурный smoke, 1–2 дня)

Сделано: 1.1 контракт уточнения на unlisted/new_wells (проза + options + accepts) · 1.4 UI кнопки/эхо/без машинных полей · 1.5 новые скважины фактами · 1.6 честные итоги commissioning и `case.finished` · **H9** контракт `needs_input` для всех инструментов (`spec_incomplete` → LLM, `ask_engineer` → человек, `human_text_problems` в обоих сервисах; `Уточните {item}` и `GNEW/GINJ/GPROD` удалены) · **1.2** композитор HITL не нужен (закрыт решением, H5) · **1.5b** Excel Extractor извлекает `new_wells` из таблицы параметров (`extract_well_parameters` → `data.excel.new_wells`), Activity и оркестратор хранят все книги кейса · машинность текста проверяется live: `run_live_five` валит кейс за snake_case/`key=value`/JSON в любом HITL и итоге.

Осталось:

1. **1.3 Единый write-path ответа.** Activity `/answer` сохраняет сырой ответ и файлы и зовёт оркестратор `resume`; нормализация и запись в журнал — только в оркестраторе. После этого `parseKeepRemove`/`parse_keep_remove` удаляются (H2, H6); в оркестраторе остаётся `choice`, а свободный текст без `choice` интерпретирует LLM (Information Extractor) в `{decision, confidence}`; деструктив при низкой уверенности — переспрос. **Проектное требование (§3 п.10):** `resume` принимает не только ответ человека, но и внешнее событие (`source: human|agent|system`, `task_id`) — путь, по которому долгий агент (расчёт на кластере) вернёт результат.
2. 🟡 **Smoke машинности на структуре** (Node smoke по сгенерированному JSON: тексты fallback-вопросов и `status_message` в Code-нодах без машинных токенов). Live-часть уже есть в `run_live_five`; структурная — вместе с 1.3.

Критерий: ✅ `run_live_five` без единого `Уточните <поле>`; ✅ combat 3 — один HITL (скважины вне Excel), параметры новых скважин из второй книги без вопросов; ⬜ один write-path, `parseKeepRemove` отсутствует в репозитории.

### Фаза 1.5 — Результаты как артефакты агентов ✅ (ревизия 6)

Зачем: UI и Activity подавали `.INC` как «итог всей работы», хотя SCHEDULE — промежуточный этап будущей цепи (кластер → tNavigator CLI → мониторинг → выгрузка результатов). Сделано до Фазы 2, чтобы `has_schedule_out` и слоты ушли одним движением. Долг §2.5 (R1–R4, O17) закрыт; бриф — `briefs/2026-09-07-phase-1.5-deliverables.md`.

Сделано:

1. **Модель артефакта** (R1): карточка `{artifact_id, role, kind: input|intermediate|deliverable, producer: user|<agent_id>, filename, mime, bytes, summary, created_at}` в обоих двойниках (`state_shape.py` ↔ `mas_state_utils.py`); `nest/flatten` без потерь (round-trip трёх видов от двух производителей — `test_state_shape.py`, `mas-orchestrator-smoke.js`). `producer`/`kind` ставит оркестратор в `Merge agent result` (`mergeIncomingArtifacts`), агенты контракт не меняли. Старые id (`excel`, `schedule_source_N`, `schedule_out`, `diff`) — это `artifact_id`/`role`, Schedule Builder и Excel без правок.
2. **Activity API** (R2): `GET /cases/{id}/artifacts?kind=&producer=`, `GET /agents` (реестр → подписи агентов для UI), snapshot и SSE отдают `artifacts[]`/`deliverables[]`; `schedule_artifact` в live API нет; `/schedule` — алиас. Артефакты, живущие в state (`schedule_out`, `diff`), отдаются без blob store.
3. **UI** (R3) — переписан целиком под ту же палитру: три колонки (задачи · рабочая область · результаты), лента как чат (группировка реплик, аватары, чипы deliverables под `agent.result` и под итогом), HITL как разговор с вариантами, композитор с состояниями «новая задача / ответ / завершено», панель «Результаты» по агентам и «Исходные данные» (`kind=input`), схема с узлами агентов из реестра и событий, пиксельно точными рёбрами, «письмами» на рёбрах и проигрыванием шагов; база знаний — вкладки агентов и поиск по карточкам. Имён агентов в UI-коде нет; в DOM «schedule» встречается только как имя файла. Статика без сборки (venv + `StaticFiles`), кэш сбивается версией в `?v=`.
4. **Оркестратор** (O17): `planner_input.deliverables: [{producer, artifact_id, kind}]`, `case.finished.payload.deliverables`; `has_schedule_out` удалён; промпт не менялся.
5. **Харнесс** (R4): `expects` в `specs()`; «`done` без ожидаемых deliverables = провал», у каждого deliverable есть `producer`; скачивание через список артефактов.
6. Тесты: `test_activity_api.py` — вместо снапшота HTML контракт (id элементов, отсутствие legacy-строк, версии статики, строки-контракты JS/CSS); `test_cases_api.py` — `/artifacts`, `/agents`, inline-артефакты; `test_state_shape.py` — round-trip карточек.

Критерий: ✅ `mas_gate.py --live` 6/6 (ревизия 6, `CASE-6a9ed4c4`…`CASE-6a9ed655`); ✅ `grep has_schedule_out` пуст; ✅ round-trip трёх видов артефактов.

### Фаза 2 — Оркестратор без домена 🟡 (осталось 4–6 дней; следующая после 1.3)

Сделано (2.0): журнал `state.ledger` (O9), `progress` в решении (O10), инварианты `answer_not_applied` (O11) / `repeat_review` + `rework_reason` (O12) / `goal_flag_ignored` (O14) / `stall_review`, `max_steps` в Runtime Config (O8), проверенное завершение `Verify completion` с детерминированным вердиктом из `goal_parts` (O16), итог инженеру без идентификаторов с fallback на журнал (O15), `nestArtifacts` без потери книг.

Осталось:

1. `agent_registry` расширить (`invoke`, `input_schema`, `output_schema`, `hitl_policy`, `enabled`, `version`); DDL в `schema` прокси.
2. `SYSTEM`: убрать «типичные пути», имена агентов, legacy-маппинг; описание агентов — только из реестра. Правила завершения (уже доменно-нейтральные) остаются.
3. Универсальный вызов `Call agent (n8n)` / `Call agent (HTTP)` по данным реестра; первый шаг — проверить на lab expression в `workflowId` executeWorkflow 1.3.
4. `MERGE` универсальный: `state.agents[<agent_id>]`; bucket'ы `excel/calc/schedule` уходят (артефакты уже в общей модели после Фазы 1.5). Журнал уже не зависит от bucket'ов.
5. **O13 `plan_update`**: схема с обязательным `id`/`title`/`agent_id`/`status`; персист; показ в Activity как декомпозиция; журнал и план — вместе в `planner_input`.
6. O4: RAG-запрос без regex-тегов; теги даёт LLM в `plan_update`.
7. RAG `orchestrator_routing`: политики вместо маршрутов.
8. Критерий: golden/combat 6/6 без циклов с промптом без слов `excel_extractor`/`schedule_builder`; тест «`echo_agent` через `upsert_agent` + импорт шаблона — вызывается без регенерации JSON».

Оценка выросла: инварианты и их smokes — отдельная работа; Qwen требует проверки каждого изменения промпта live-прогоном.

### Фаза 3 — Агенты LLM-first ✅ ядро (Schedule Builder ✅, Excel ✅; хвосты 3.5/3.6 ⬜)

1. ✅ **A9/A3/A4 Schedule Builder**: `suggested_capability`, Capability router и HTTP-обход удалены; `apply_group_rebind(spec)` от LLM (`wells, parent_group, parent_of_parent, control, gas_rate, effective_at`), неполный spec → `spec_incomplete` LLM; `GROUP_INTENT`, `_parse_rate`, авто-`G{well}` из regex удалены (конвенции — явные `assumptions`). `SYSTEM` — «какой инструмент когда», `ask_engineer` — единственный путь к человеку.
2. ✅ **A11 платформа**: инструменты агентов — `n8n-nodes-base.httpRequestTool` + `$fromAI` (`mas_tool_nodes.py`); старый `toolHttpRequest` в 2.30.8 не исполняется. Любой новый инструмент — только через `tool_http(...)` генератора.
3. ✅ Excel Extractor LLM-first: `open_session` забирает все Excel-вложения кейса в одну сессию (несколько `.xlsx` склеиваются, лист = `<лист> (<файл>)`) и отдаёт **инвентарь** (файлы, листы, таблицы, колонки, 2 строки-образца) + `engineer_answers` + `rework_reason`; LLM выбирает таблицу/колонки и вызывает `extract_commissioning(table_id, well_column, date_column)` / `extract_well_parameters(table_id, well_column, mapping)` (детерминированное извлечение, валидация колонок, `too_many_attempts` после 3 попыток) или `ask_engineer` (проза, `question_not_human`). Регекс `_COMMISSIONING_RE`, `suggested_capability`, Capability router, `Extract commissioning`/`Describe extract result` из workflow удалены. Опциональный `mapping` идёт JSON-текстом (`$fromAI` не принимает пустой json) — Python парсит строку.
4. ✅ Python `/agent/run` и `/agent-tools/extract_commissioning`(без аргументов) у Excel удалены; extract-инструменты — обычные registry-tools через `/agent-tools/{name}`.
5. 🟡 Надёжность tool-calls Qwen: ✅ `maxIterations` 8 у обоих агентов; ✅ `SUMMARIZE_AI` даёт прозу при «агент крутил inspect без apply»; ✅ протокол «один результат на сессию» (`result_already_stored`) и `too_many_attempts`; ⬜ **3.5** smoke «агент вызвал инструмент, а не ответил текстом» (по `intermediateSteps` в live-трассе и по структуре генератора).
6. ⬜ **3.6** A6 `INTENT_ALIASES` → выборка RAG `keyword_instruction` (низкий приоритет: это подсказка поиска, не выбор инструмента).
7. Критерий: ✅ golden 2 без `GROUP_INTENT`; ✅ combat 0–3 через LLM-выбор инструмента; ✅ combat 0–3 без `_COMMISSIONING_RE` (Excel); `.INC` сравнивается **семантически** (по решению заказчика: keyword на нужной дате с теми же параметрами; пробелы/пустые строки/формат чисел не важны — `compare_schedules` канонизирует токены записей по шагу DATES); лишних HITL в combat 3 нет — вопрос про скважины вне Excel задаёт LLM прозой, harness отвечает кнопкой.

### Фаза 4 — Расширяемость как продукт ⬜ (3–4 дня, после Фазы 2)

Activity «Агенты» (список, карточка, health, `workflow_id`, «добавить из шаблона»), шаблон агента под `agent_task/agent_result` + FastAPI-скелет (Windows `.bat`), `agent_card` в базе знаний. Уже есть: рецепты в `AGENTS.md` §6 и правилах по областям; `tool_http(...)` как единственный способ дать агенту инструмент. Критерий: новый агент «Расчёт КИН» за час без правок n8n JSON.

### Фаза 5 — Петля качества 🟡 (постоянно)

Есть: `scripts/mas_gate.py` (один вердикт offline/live) и `scripts/mas_trace_case.py` (трасса кейса с аудитом текста и подсказками цикл/повтор/review); `run_live_five` как гейт с проверкой циклов, повторных вопросов, review-эскалаций, бюджета шагов, `done` без `.INC`, машинного текста; smoke оркестратора воспроизводит цикл `CASE-6a9da4e2`, ложные finish `CASE-6a9dac9d`/`CASE-6a9dc4b3`/`CASE-6a9e46ea`, самопротиворечивую верификацию и потерю второй книги как регресс-сценарии; pytest-регрессы с id кейсов в docstring (A12, A13).

Добавить: структурный smoke машинности (Фаза 1 п.2); smoke tool-calls (Фаза 3.5); LLM-судья читаемости ленты (`test_eval_judge.py` из Excel Tools); метрики в Activity (шаги/кейс, HITL/кейс, время до `.INC`, `guard`-срабатывания — их число должно падать до нуля по мере того, как LLM учится завершать сама).

---

## 5. Что остаётся детерминированным (явно)

`parse_schedule`, `apply_operations`, `emit_schedule`, INCLUDE-резолв, `schema_catalogues.json` + `render`, защита фактических `WCONPROD`, retarget дат и `compose_new_well_lines` в `timeline_ops`, порядок скважин в записях по baseline, геометрия Math, allowlist keywords, DDL прокси, **инварианты завершения** (§3 п.7) и бюджеты шагов/ошибок из Runtime Config.

Инварианты — не «регулярки вместо LLM»: они не знают домена и не выбирают агента; они защищают протокол (ответ доходит до спросившего, повтор не исполняется молча, лимит — эскалация). Правило `.cursor/rules/mas-llm-first-no-domain-hardcode.mdc` это разрешает.

---

## 6. Риски (обновлены)

| Риск | Мера |
|---|---|
| Expression в `workflowId` executeWorkflow 1.3 в 2.30.8 | Первый шаг Фазы 2; fallback — слоты `Call agent slot 1..N` с id из Runtime Config |
| Qwen нестабильно вызывает tools | Structured Output + `autoFix`, `maxIterations` 8, ошибки аргументов возвращаются LLM с подсказкой, ⬜ smoke на tool-calls (3.5); «LLM решает → детерминированный инструмент исполняет» |
| 🆕 Инструмент агента на узле, который 2.30.8 не исполняет (`toolHttpRequest`, A11) | Только `n8n-nodes-base.httpRequestTool` через `tool_http(...)`; реестр допустимых узлов/версий в `test_workflow_contracts.py`; любой новый тип узла — сначала в реестр |
| 🆕 Qwen галлюцинирует завершение/результат даже с журналом | Инварианты §3 п.7 (сработали в `CASE-6a9dac9d`); `guard` в payload для аудита; метрика срабатываний |
| 🆕 Правка промпта оркестратора ломает поведение незаметно для smokes | Live-гейт обязателен перед «готово»; smoke воспроизводит известные трассы |
| 🆕 Два write-path ответов человека | Журнал реконсилируется из `hitl.answers`; целевое — один путь (Фаза 1.3) |
| LLM-интерпретация свободного ответа ошибётся на деструктиве | Кнопки дают `choice`; свободный текст — `confidence ≥ 0.8` иначе переспрос |
| Регресс golden `.INC` | Семантическое сравнение (`compare_schedules`: keyword на дате + канонизированные записи) в каждой фазе; порядок записей детерминирован; goldens не переписываются без явного решения заказчика |
| 🆕 Правки JSON workflow руками или без регенерации | `scripts/mas_gate.py` регенерирует всё и показывает дрейф; правило «генерируем, не редактируем» в `AGENTS.md` |
| Полевой UI-импорт: новые бинды | Всё новое — реестр/Runtime Config/Credentials; Health Check проверяет бинды |

---

## 7. Журнал изменений по плану

### Ревизия 2 (2026-09-06, вечер) — сделано

1. Фаза 0 целиком.
2. HITL: кнопки-варианты, `choice`+`label`, без машинных полей в DOM; unlisted и new_wells — прозой с вариантами/таблицей; новые скважины — факты, `compose_new_well_lines` по мануалу; честные итоги commissioning; `case.finished` — итог оркестратора.
3. O2-hint удалён → **инцидент**: цикл `CASE-6a9da4e2` (23 повтора, `failed` по лимиту 24). Причина — отсутствие памяти о результатах. Починено «грамотным завершением» (§3 п.7, `docs.md` §1.3). Второй инцидент в чистом прогоне — ложный `finish` после ответа человека → инвариант `answer_not_applied`.
4. `max_steps` в Runtime Config; `run_live_five` валит кейс за цикл/эскалацию/`failed`.
5. Детерминированный порядок скважин в group_rebind.
6. Live: 6/6 `ok`, `mismatch_count: 0`, без циклов. Наблюдение для Фазы 1/3: combat 3 — два лишних HITL «Уточните rate/wells» от `apply_group_rebind` (H9/A9).

### Ревизия 3 (2026-09-07, ночь) — сделано

1. H9 + A3/A4/A5/A8/A9: Schedule Builder LLM-first (см. §2.3), `ask_engineer`, `spec_incomplete`, `human_text_problems`; Python `/agent/run` Schedule удалён.
2. **A11 — платформенная находка**: первый же live-прогон LLM-пути упал на `toolHttpRequest` («has a supplyData method but no execute method»). В 2.30.8 узел скрыт и не исполняется AI Agent v3; regex-роутер маскировал это с самого начала. Оба агента переведены на `httpRequestTool` + `$fromAI`.
3. O14 — ложный `finish` `CASE-6a9dc4b3`: guard `goal_satisfied` доверял флагу, а не действию → теперь только при повторном делегировании. O15 — итог для инженера без идентификаторов (промпт + fallback на журнал).
4. A12 — remove-политика удаляла wildcard-записи (`WEFAC '*'`); исправлено, pytest-регресс.
5. `run_live_five`: проверка «машинности» каждого HITL/итога; падение сразу при повторе того же вопроса после ответа и при `done` без `.INC`; ответ на вопрос агента распознаётся по вариантам «оставить/убрать», как это сделал бы инженер.
6. O16 — проверенное завершение (`Verify completion`): live-гейт после O14 показал, что LLM сама выбирает `finish` после Excel (2 из 6 кейсов) — флаговые guard'ы тут бессильны. Завершение теперь подтверждает отдельный скептический LLM-проход по журналу; отказ даёт оркестратору ещё шаг, второй отказ — инженеру. Детерминированная часть: вердикт выводится из `goal_parts` (модель противоречила себе: все части covered, `all_covered:false` — golden 1 ушёл в review); `unsupported_claims` → инженеру показывается итог по журналу, не текст LLM. A13 — попутно найденный дубль вопроса про новые скважины. Итог гейта: 6/6 `done`, `completion_verified:true`, `.INC` побайтно.

### Ревизия 4 (2026-09-07, день) — сделано

1. **Excel Extractor LLM-first (A1/A2/A7/A8, Фаза 3.3/3.4).** Live-проба показала: `handoff_message` оркестратора всегда содержал «даты ввода», поэтому `_COMMISSIONING_RE` уводил каждую задачу в HTTP-обход, LLM-путь Excel в проде не исполнялся ни разу; имя файла терялось (`inputs.artifacts` удаляется оркестратором); второй `.xlsx` кейса пропадал в `nest_artifacts`. Сделано: инвентарь из `open_session` (все книги кейса), инструменты `extract_commissioning(table_id, well_column, date_column)`, `extract_well_parameters(table_id, well_column, mapping)`, `ask_engineer`; ошибки выбора — LLM с `available_*`; итоги — факты, не «вызвал N tools». Activity и оркестратор (`nestArtifacts` в `mas_state_utils.py`) хранят все Excel-вложения (`excel`, `excel_1` → `attachments[role=excel]`); первый live-прогон case 3 поймал именно JS-вариант потери (`CASE-6a9e67ef`). Live-гейт после правки: 6/6 `done`, `mismatch_count: 0`, combat 3 — один HITL (скважины вне Excel), параметры новых скважин прочитаны из второй книги. Schedule Builder читает `new_wells` из `data.excel` (после ответа инженера, до `inputs`). Фикстура `case3_new_wells_params.xlsx` получила колонки I/J.
2. **Семантическое сравнение `.INC`** (решение заказчика): `compare_schedules` сравнивает по шагам DATES набор keyword и мультимножество канонизированных записей (кавычки, регистр, `0.150`=`0.15`, хвостовые `1*`, порядок записей внутри keyword). Пробелы/пустые строки/формат чисел — не различие.

### Ревизия 5 (2026-09-07, день) — слой знаний для делегирования

Цель: чтобы следующий пункт плана мог выполнить другой (более дешёвый) агент без повторного исследования репозитория. Сделано:

1. `AGENTS.md` — точка входа: карта репозитория, 11 инвариантов, контракты, цикл одной задачи, рецепты расширения, ловушки (A11 `toolHttpRequest`, regex-маскировка, флаги Qwen, двойники `nest_artifacts`, `_MACHINE_TOKEN_RE`, venv для pytest, семантическое сравнение `.INC`), правила делегирования.
2. `.cursor/rules/`: всегда — `mas-working-contract` (доказательство → правка источника → гейт → фиксация в плане), плюс существующие 4; по файлам — `excel-agent-tools`, `schedule-builder-service`, `n8n-templates`, `mas-activity-service`, `live-harness`. `.gitignore` теперь версионирует `.cursor/rules` и `.cursor/skills` (локальный `mcp.json` — нет).
3. `.cursor/skills/`: `/mas-gate` (как гонять и читать гейт), `/mas-trace` (как читать трассу кейса, таблица «симптом → узел»), `/mas-brief` (шаблон брифа для делегирования одного пункта плана).
4. `scripts/mas_gate.py` — один вердикт: регенерация всех workflow с проверкой дрейфа (порядок: генераторы → `generate_schedule_workflows.py` с relayout последним; `versionId` игнорируется), 13 smokes, 3 pytest-набора (150/57/87), offline combat; `--live` — redeploy + `run_live_five`. `scripts/mas_trace_case.py` — лента, state/ledger, HITL, аудит машинного текста, подсказки (цикл, повтор вопроса, review, бюджет шагов, `done` без `.INC`), n8n executions и вывод узла. Проверено: offline-гейт GREEN, `--live --cases golden_case_1` GREEN (`CASE-6a9e7158-bc224c`).
5. Файл плана сверен с кодом 1:1 (статусы H5/H9/A6/O1/O3, контракт `agent_result`, критерии Фазы 1, риски: семантическое сравнение, A11).
6. Решение заказчика: SCHEDULE — промежуточный этап, результат кейса — deliverables агентов (§3 п.9–10). Добавлены §2.5 (R1–R4, O17), Фаза 1.5 (до Фазы 2), требование к 1.3 про внешний `resume`; бриф `briefs/2026-09-07-phase-1.5-deliverables.md`.

### Ревизия 6 (2026-09-07, вечер) — Фаза 1.5 + UI + два live-дефекта

1. **Модель артефактов / API / харнесс** (R1–R4, O17): карточки `kind`/`producer` в Python и JS; `GET /cases/{id}/artifacts`, `GET /agents`; `planner_input.deliverables`; `has_schedule_out` удалён; `run_live_five` ждёт `expects` и качает через список артефактов.
2. **UI** переписан без сборки (venv + `StaticFiles`): три колонки, чат с чипами deliverables, панель «Результаты» по агентам, схема из реестра, база знаний с вкладками и поиском. Имён агентов в UI-коде нет.
3. Live-гейт на новой модели (первый прогон) — 4/6: `golden_case_1` `CASE-6a9ec5ef-905bb0` ушёл в `completion_review` («получить старый прогнозный файл» как часть цели); `combat_case1` `CASE-6a9ec6b3-74e34e` — 5 семантических mismatch: Decision LLM положила в `action.task.facts` выдуманные даты (`295R`/`1601` → OCT/DEC 2019 вместо Excel `2020-02-01`), `Parse decision` копировал `action.task` в `agent_task.inputs`, Schedule Builder предпочёл их `data.excel.facts`. Combat 0/2/3 и golden 2 — `ok`, `mismatch_count: 0`.
4. Правки: журнал шаг 0 = вложения инженера (имена файлов); `VERIFY_SYSTEM` не выделяет исходные файлы в часть цели; `agent_task.inputs` — только ссылки оркестратора (`handoff_message` — единственный канал к агенту). Smoke: `CASE-6a9ec6b3` (выдуманные `facts` не доходят) и `CASE-6a9ec5ef` (шаг 0 в `planner_input`).
5. Повторный `--live` GREEN 6/6, `mismatch_count: 0`, без `completion_review`: `CASE-6a9ed4c4-26f1a4` (golden 1, finish без review), `CASE-6a9ed4fc-2bf47a`, `CASE-6a9ed54e-594585`, `CASE-6a9ed57d-e860b7` (combat 1: `1601`/`295R` → `1 FEB 2020` из Excel, не OCT/DEC), `CASE-6a9ed5be-7c5ab2`, `CASE-6a9ed655-43325b`.

### Ближайшие шаги (в этом порядке)

Каждый пункт — отдельный бриф по `/mas-brief` (файл в `briefs/`); перед стартом и в конце — `python3 scripts/mas_gate.py [--live]`.

1. Фаза 1.3 — единый write-path ответа с внешним `resume` (Activity хранит сырой ответ → оркестратор интерпретирует; `parseKeepRemove`/`parse_keep_remove` удалить) + структурный smoke машинности (Фаза 1 п.2).
2. Фаза 2 — реестр исполняемый, промпт без маршрутов (O1/O3/O5), `plan_update` с `id` (O13), RAG без regex-тегов (O4); `MERGE` универсальный поверх общей модели артефактов (Фаза 1.5 сделана).
3. Фаза 3.5 — smoke «агент вызвал инструмент, а не ответил текстом»; 3.6 — `INTENT_ALIASES` → RAG (A6).
4. Фаза 4 — Activity «Агенты» и шаблон агента (после Фазы 2).
5. Фаза 5 — метрики `guard`-срабатываний в Activity; LLM-судья читаемости ленты.
