# NOVATEK RE MASter — анализ и план рефакторинга «от хардкода к инженерной MAS»

Ревизия 2 — 2026-09-06 (вечер). Ревизия 1 — утро того же дня. Основание ревизии 2: выполнены Фаза 0 и «быстрые победы», три live-прогона `run_live_five.py` (один — с зацикливанием оркестратора, два — чистых), разбор трасс кейсов `CASE-6a9da4e2` (цикл), `CASE-6a9dac9d` (ложный finish), `CASE-6a9dafa5` (машинные вопросы от инструмента).

Ограничения, которые план не нарушает: n8n **2.30.8**, только UI (Import from File, Credentials, Set-ноды); FastAPI на Windows — только Python; вся правка адресов и лимитов — в `MAS — Runtime Config`; секретов в JSON нет.

Легенда статусов: ✅ сделано · 🟡 частично · ⬜ не начато · 🆕 найдено в ревизии 2.

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
| 🆕 Журнал задачи (`state.ledger`) + `progress` в решении + предохранители в `Parse decision` | Завершение — осознанное решение по фактам, а цикл невозможен молча (`docs.md` §1.3) |
| 🆕 `run_live_five.py` как регресс-гейт с проверкой циклов | Кейс проваливается за повторный handoff без нового ввода, review-эскалацию, `step_count > 8`, статус `failed` |

---

## 2. Инвентарь хардкода и «машинных директив» — со статусами

### 2.1 Оркестратор

| # | Где | Что | Статус |
|---|---|---|---|
| O1 | `SYSTEM` | Агенты поимённо; «типичный путь excel_extractor → schedule_builder»; legacy-маппинг `*_specialist` | ⬜ Правило «schedule_out есть — finish» заменено журналом; «типичные пути» и имена агентов ещё в промпте (Фаза 2) |
| O2 | `Prepare decision context` | Rule-based hint «следующий шаг» | ✅ Удалён; smoke проверяет отсутствие маршрутных подсказок в `planner_input` |
| O3 | `ROUTE`, `MERGE` | `if agent_id==='excel_extractor'…`; bucket `excel/calc/schedule` | ⬜ Фаза 2 |
| O4 | `mas_state_utils.py` `inferRouting*` | Regex по goal для RAG-фильтров | ⬜ Фаза 2 |
| O5 | 8 routing-карточек RAG | Дублируют O1 | ⬜ Фаза 2 |
| O6 | `DECISION_SCHEMA` | `options[]` терялись по дороге к человеку | 🟡 UI рендерит `options[{value,label}]` кнопками; `question_id` LLM-вопросов всё ещё свободный |
| O7 | `specialist_packet` и retired-контракты | Два контракта | ✅ Не принимаются; retired в `n8n/templates/retired`, `n8n/contracts/retired` |
| O8 | `MERGE` лимиты | `step_count >= 24` захардкожен, при достижении — тихий `failed` | ✅ `max_steps` в Runtime Config (12); при достижении с результатом — review-гейт с человеком, без результата — честный `failed` |
| 🆕 O9 | Весь оркестратор | **Нет памяти о результатах агентов**; `current_task` + флаги вместо истории → цикл `CASE-6a9da4e2` | ✅ Журнал `state.ledger.history` (агенты + человек), блок «Журнал задачи» в `planner_input`, `compact.journal` |
| 🆕 O10 | `DECISION_SCHEMA` / `Parse decision` | Нет критерия завершения: LLM выбирала `finish`/`call_agent` «по привычке» | ✅ Обязательный `progress {goal_satisfied, evidence, missing, is_repeating}`; `finish` при `goal_satisfied`, `summary_for_human` из summary агентов |
| 🆕 O11 | `Parse decision` | Ложный `finish` после ответа человека (`CASE-6a9dac9d`): агент спросил → человек ответил → LLM «завершила», не вернув ответ агенту | ✅ Инвариант `answer_not_applied`: ответ уходит агенту, который спросил; `finish` невозможен, пока он не вернул `completed` |
| 🆕 O12 | `Parse decision` | Повторное делегирование после `completed` исполнялось молча | ✅ Один повтор только с `rework_reason` (уходит агенту), дальше — review-гейт `result_approval`; ответ человека сбрасывает stall |
| 🆕 O13 | `Parse decision` `plan_update` | Мержится по `item.id`, а LLM пишет `{step, action, agent_id, reason}` → `plan` всегда `[]`, декомпозиция не сохраняется | ⬜ Фаза 2.5: схема `plan_update` с обязательным `id`, показ в Activity |

### 2.2 HITL

| # | Где | Что | Статус |
|---|---|---|---|
| H1 | `timeline_ops.py` unlisted | `expected_format: "keep\|remove"`, `enum` | ✅ Проза + `options[{value,label,hint}]`, `accepts.free_text` |
| H2 | `parseKeepRemove` и зеркало в Activity | Regex по ответу человека | 🟡 Кнопка даёт `choice` — regex не нужен; для свободного текста regex остался как fallback до 1.3 |
| H3 | `new_well_defs` со строками `.inc` | Человек пишет SCHEDULE руками | ✅ Инженерные факты `new_wells` (группа, MD верх/низ, диаметр, режим+дебит, BHP, VFP, файл WELLTRACK); `compose_new_well_lines` собирает `WELSPECS/COMPDATMD/WCONPROD` по мануалу; недостающий факт = finding. Legacy typed-lines принимается для совместимости |
| H4 | combat-3: вопрос про новые скважины не задавался | Диалог не соответствовал уточнениям | ✅ Два последовательных вопроса (unlisted → new_wells), harness отвечает на каждый отдельно |
| H5 | `hitl_user_copy.py` + `humanizeQuestion` | Два слоя «компиляции» кодов в русский | ⬜ Пересмотрено: если инструменты отдают прозу (H9), композитор не нужен; `hitl_user_copy` остаётся fallback для кодов ошибок |
| H6 | Activity `/answer` пишет в state напрямую, оркестратор — через `resume` | Два write-path | 🟡 Журнал подхватывает ответы из `hitl.answers` независимо от пути (`reconcileLedgerAnswers`); сами два пути остались (Фаза 1.3) |
| H7 | `app.js renderGate` | Без кнопок, `kind` всегда `needs_input` | ✅ Кнопки, `choice`+`label`, эхо «Вы решили: …», `kind` из вопроса (`result_approval` для review), без `expected_version`/`gate_id` в DOM; подсказка колонок таблицы для `accepts.table` |
| H8 | `agent.result` шаблон `DESCRIBE_APPLY` | Лента врала про сделанное | ✅ `summarize_commissioning_result` по фактическому diff (сдвинуто/добавлено/убрано/оставлено); `agent.result` эмитит только оркестратор |
| 🆕 H9 | `agent_tools.py` `apply_group_rebind` (и `main.py`) | `needs_input` с `requests=[{question:"Уточните {item} для перепривязки групп"}]` по `missing` → человек читает «Уточните rate» (`CASE-6a9dafa5`, два раунда) | ⬜ **Приоритет.** Контракт: инструмент с неполным spec возвращает finding **для LLM агента** (что не хватает, откуда взять — GRUPTREE, задача), а не вопрос человеку; вопрос человеку — только когда данных нет ни в задаче, ни в baseline, и тогда прозой с `options`/`accepts` |
| 🆕 H10 | Activity `case.finished` | Текст «Задача завершена. Загрузите результаты работы.» затирал итог оркестратора | ✅ Показывается `status_message` оркестратора (итог по журналу), шаблон — fallback |

### 2.3 Агенты и FastAPI

| # | Где | Что | Статус |
|---|---|---|---|
| A1 | Excel `_COMMISSIONING_RE` → HTTP без LLM | LLM-substitute | ⬜ Фаза 3 |
| A2 | `WELL_COL`/`DATE_COL` substring | Выбор колонок без подтверждения LLM | ⬜ Фаза 3 (как `suggested_mapping`) |
| A3 | Schedule `suggested_capability` | Regex `GROUP_INTENT`, «есть facts → commissioning» | ⬜ Фаза 3 |
| A4 | `group_rebind.py` `extract_group_rebind_spec`, `_parse_rate`, `G{well}` | Регулярки вместо структурированного вывода | ⬜ Фаза 3 |
| A5 | Fallback `["GNEW","GINJ","GPROD"]`, «Уточните {item}» | Заглушки вместо GRUPTREE | ⬜ Сливается с H9 |
| A6 | `INTENT_ALIASES` | Словарь keyword | ⬜ Фаза 3 |
| A7 | `SUMMARIZE_AI`/`DESCRIBE_*` | Машинные итоги | 🟡 Commissioning — честный diff; group_rebind и Excel — ещё шаблоны |
| A8 | Python `/agent/run` дубли | Второй источник поведения | ⬜ Фаза 3 |
| 🆕 A9 | Schedule Builder LLM на commissioning-задаче вызвала `apply_group_rebind` (`CASE-6a9dafa5`) | Промпт агента/описания инструментов не удерживают LLM от «попробовать всё»; результат вышел верным только благодаря повторным HITL | ⬜ Фаза 3.3/3.4 — начать с этого |
| 🆕 A10 | `group_rebind_revise` | Порядок скважин в записях зависел от порядка в spec от LLM (golden 2 мигал: 1602 перед 1601) | ✅ Детерминированный порядок по первому появлению в baseline |

### 2.4 Расширяемость и гигиена

- Добавить агента сегодня = правки `SYSTEM`, `ROUTE`, узел вызова, `MERGE` bucket, sticky, RAG-карточка, SQL seed, agent workflow — ⬜ Фаза 2/4.
- `KEYWORDS` один источник — ✅. Retired-контур вне live-генерации/smokes — ✅. Legacy Activity `/v1/tasks*` — 🟡 задокументирован в `main.py`, вынос отложен (связность тестов).

---

## 3. Целевая архитектура (принципы, дополнены)

1. **Оркестратор не знает домена.** Промпт — роль, формат решения, правила безопасности. Доменное — из `agent_registry` и RAG `orchestrator_routing` (политика, не маршрут).
2. **Реестр исполняемый.** `invoke`, `input_schema`, `output_schema`, `hitl_policy` в строке реестра; универсальный узел вызова; новый агент без правки оркестратора.
3. **Агент = LLM с инструментами, инструменты детерминированы.** Решение «какой инструмент и с чем» — LLM агента через Structured Output; regex-роутеры и `_parse_*` уходят.
4. **HITL — разговор инженеров.** Вопрос — проза + `options[{value,label,hint}]` + `accepts{free_text, files, table}`; кнопки в UI; ответ — `choice` + свободный текст; инженер даёт факты/таблицы/файлы, не `.INC`. **Дополнение:** это контракт **каждого инструмента**, не только агента: `needs_input` без прозы и вариантов — дефект.
5. **Один контракт агента.** `agent_task` → `agent_result {status, summary_for_human, data, artifacts, requests[], issues}`.
6. **Лента — источник правды.** `summary_for_human` по фактическому diff; `case.finished` — итог по журналу.
7. 🆕 **Оркестратор помнит и проверяет.** Журнал (кто что сделал, что ответил человек) — единственная основа для решения о завершении. Поверх LLM — доменно-нейтральные инварианты: ответ человека доходит до спросившего; повтор без нового ввода — review, не исполнение; `goal_satisfied` ⇒ `finish`; бюджет шагов — эскалация к человеку, не тихий `failed`. Каждый инвариант — с `guard` в `orchestrator.decision.payload` для аудита.
8. 🆕 **Live-гейт после каждой правки оркестратора/агентов.** Smokes проверяют структуру, но цикл и галлюцинацию ловит только живой прогон с проверкой «завершение осознанное»: `run_live_five.py` обязателен перед «готово».

---

## 4. План по фазам (ревизия 2)

Каждая фаза проверяется: smokes (13 live) + pytest (Activity 149, Schedule 50, Excel 87) + `run_live_five.py` 6/6 без циклов. Порядок пересобран: сначала дефекты, которые видны инженеру в ленте прямо сейчас (H9/A9), затем оркестратор без домена.

### Фаза 0 — Гигиена ✅

Сделано: retired-контур вынесен (`n8n/templates/retired`, `n8n/tests/retired`, `n8n/contracts/retired`), один `KEYWORDS`, `specialist_packet` не принимается, правило `mas-llm-first-no-domain-hardcode.mdc`, Health Check берёт URL из Runtime Config. Хвост: legacy Activity API — вынос при удобном случае.

### Фаза 1 — Человеческий HITL 🟡 (осталось 2–3 дня)

Сделано: 1.1 контракт уточнения на unlisted/new_wells (проза + options + accepts) · 1.4 UI кнопки/эхо/без машинных полей · 1.5 новые скважины фактами · 1.6 честные итоги commissioning и `case.finished`.

Осталось, в порядке приоритета:

1. **H9 — контракт `needs_input` для всех инструментов** (Schedule: `apply_group_rebind`, `apply_commissioning`, gap-вопросы; Excel: `clarification`). Правило: неполный spec → `issues[]` для LLM агента с указанием, где взять данные (задача, GRUPTREE, baseline); вопрос человеку — только при реальном отсутствии данных, прозой с вариантами. Убрать `Уточните {item}` и fallback `GNEW/GINJ/GPROD`.
2. **Smoke «нет машинных токенов в тексте для человека»**: `hitl.request.question`, `options[].label`, `status_message`, `summary_for_human`, `case.finished` — без `[a-z_]+=[a-z]+`, snake_case-идентификаторов, JSON. Гейт и на структуру (Node smoke по JSON агентов/оркестратора), и на live-трассу (проверка в `run_live_five`).
3. **1.3 Единый write-path ответа.** Activity `/answer` сохраняет сырой ответ и файлы и зовёт оркестратор `resume`; нормализация и запись в журнал — только в оркестраторе. После этого `parseKeepRemove` в Activity удаляется; в оркестраторе остаётся `choice`, а свободный текст без `choice` интерпретирует LLM (Information Extractor) в `{decision, confidence}`; деструктив при низкой уверенности — переспрос.
4. **1.2 HITL-composer — понижен.** Отдельный LLM-шаг для переписывания вопросов не нужен, если инструменты (п.1) отдают прозу. Композитор нужен только для вопросов самого оркестратора (`ask_user` LLM) — там достаточно требования схемы: `question` прозой, `options[{value,label}]`.
5. Excel Extractor извлекает `new_wells` из приложенной таблицы параметров (сейчас факты приходят JSON из харнесса) — вместе с Фазой 3.2.

Критерий: `run_live_five` без единого `Уточните <поле>`; combat 3 — ровно два HITL (unlisted, new_wells); smoke машинности зелёный.

### Фаза 2 — Оркестратор без домена 🟡 (осталось 5–7 дней; было 4–6)

Сделано (2.0, вне ревизии 1): журнал, `progress`, инварианты `answer_not_applied` / `human_accepted` / `goal_satisfied` / `repeat_review` / `stall_review`, `max_steps` в Runtime Config, честный `finish`.

Осталось:

1. `agent_registry` расширить (`invoke`, `input_schema`, `output_schema`, `hitl_policy`, `enabled`, `version`); DDL в `schema` прокси.
2. `SYSTEM`: убрать «типичные пути», имена агентов, legacy-маппинг; описание агентов — только из реестра. Правила завершения (уже доменно-нейтральные) остаются.
3. Универсальный вызов `Call agent (n8n)` / `Call agent (HTTP)` по данным реестра; первый шаг — проверить на lab expression в `workflowId` executeWorkflow 1.3.
4. `MERGE` универсальный: `state.agents[<agent_id>]` + `artifacts` merge; bucket'ы `excel/calc/schedule` уходят. Журнал уже не зависит от bucket'ов.
5. **O13 `plan_update`**: схема с обязательным `id`/`title`/`agent_id`/`status`; персист; показ в Activity как декомпозиция; журнал и план — вместе в `planner_input`.
6. O4: RAG-запрос без regex-тегов; теги даёт LLM в `plan_update`.
7. RAG `orchestrator_routing`: политики вместо маршрутов.
8. Критерий: golden/combat 6/6 без циклов с промптом без слов `excel_extractor`/`schedule_builder`; тест «`echo_agent` через `upsert_agent` + импорт шаблона — вызывается без регенерации JSON».

Оценка выросла: инварианты и их smokes — отдельная работа; Qwen требует проверки каждого изменения промпта live-прогоном.

### Фаза 3 — Агенты LLM-first ⬜ (5–7 дней) — начать со Schedule Builder

1. **A9/A3/A4 Schedule Builder первым**: убрать `suggested_capability`; `apply_group_rebind(spec)` принимает структурированный spec от LLM (Structured Output из задачи + `inspect_schedule`: реальные группы GRUPTREE как варианты); удалить `GROUP_INTENT`, `_parse_rate`, quoted-parent regex, авто-`G{well}`. Описания инструментов в промпте агента: когда какой уместен, «не пробовать инструмент ради проверки».
2. Промпты агентов: без «при suggested_capability=… вызови X и STOP»; роль, инварианты (не писать `.INC` руками, факт ≠ приказ, METRIC из baseline), `summary_for_human` по diff — для group_rebind и Excel тоже (A7).
3. Excel Extractor: LLM выбирает таблицу/колонки через `detect_tables`/`describe_table` (`WELL_COL`/`DATE_COL` → `suggested_mapping`), затем детерминированный `extract_facts`; извлечение `new_wells` из таблицы параметров.
4. Убрать Python `/agent/run` дубли (A8) у Excel/Schedule; оставить у Math.
5. Надёжность tool-calls Qwen: лимит итераций, `autoFix`, smoke «агент вызвал инструмент, а не ответил текстом».
6. Критерий: golden 2 без `GROUP_INTENT`; combat 0–3 без `_COMMISSIONING_RE`; `.INC` побайтно тот же; ни одного лишнего HITL в combat 3.

### Фаза 4 — Расширяемость как продукт ⬜ (3–4 дня)

Без изменений: Activity «Агенты» (список, карточка, health, `workflow_id`, «добавить из шаблона»), шаблон агента под `agent_task/agent_result` + FastAPI-скелет (Windows `.bat`), `agent_card` в базе знаний. Критерий: новый агент «Расчёт КИН» за час без правок n8n JSON.

### Фаза 5 — Петля качества 🟡 (постоянно)

Есть: `run_live_five` как гейт с проверкой циклов/эскалаций/бюджета шагов; smoke оркестратора воспроизводит цикл `CASE-6a9da4e2` и ложный finish `CASE-6a9dac9d` как регресс-сценарии.

Добавить: smoke машинности (Фаза 1.2); LLM-судья читаемости ленты (`test_eval_judge.py` из Excel Tools); метрики в Activity (шаги/кейс, HITL/кейс, время до `.INC`, `guard`-срабатывания — их число должно падать до нуля по мере того, как LLM учится завершать сама).

---

## 5. Что остаётся детерминированным (явно)

`parse_schedule`, `apply_operations`, `emit_schedule`, INCLUDE-резолв, `schema_catalogues.json` + `render`, защита фактических `WCONPROD`, retarget дат и `compose_new_well_lines` в `timeline_ops`, порядок скважин в записях по baseline, геометрия Math, allowlist keywords, DDL прокси, **инварианты завершения** (§3 п.7) и бюджеты шагов/ошибок из Runtime Config.

Инварианты — не «регулярки вместо LLM»: они не знают домена и не выбирают агента; они защищают протокол (ответ доходит до спросившего, повтор не исполняется молча, лимит — эскалация). Правило `.cursor/rules/mas-llm-first-no-domain-hardcode.mdc` это разрешает.

---

## 6. Риски (обновлены)

| Риск | Мера |
|---|---|
| Expression в `workflowId` executeWorkflow 1.3 в 2.30.8 | Первый шаг Фазы 2; fallback — слоты `Call agent slot 1..N` с id из Runtime Config |
| Qwen нестабильно вызывает tools | Structured Output + `autoFix`, лимит итераций, smoke на tool-calls; «LLM решает → детерминированный инструмент исполняет» |
| 🆕 Qwen галлюцинирует завершение/результат даже с журналом | Инварианты §3 п.7 (сработали в `CASE-6a9dac9d`); `guard` в payload для аудита; метрика срабатываний |
| 🆕 Правка промпта оркестратора ломает поведение незаметно для smokes | Live-гейт обязателен перед «готово»; smoke воспроизводит известные трассы |
| 🆕 Два write-path ответов человека | Журнал реконсилируется из `hitl.answers`; целевое — один путь (Фаза 1.3) |
| LLM-интерпретация свободного ответа ошибётся на деструктиве | Кнопки дают `choice`; свободный текст — `confidence ≥ 0.8` иначе переспрос |
| Регресс golden `.INC` | Побайтное сравнение в каждой фазе; порядок записей детерминирован |
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

### Ближайшие шаги (в этом порядке)

1. H9 — контракт `needs_input` инструментов Schedule Builder (без «Уточните <поле>»), smoke машинности.
2. A9/A3 — Schedule Builder LLM-first: `suggested_capability` убрать, spec group_rebind от LLM структурой, описания инструментов.
3. Фаза 1.3 — единый write-path ответа.
4. Фаза 2 — реестр исполняемый, промпт без маршрутов, `plan_update` с `id`.
