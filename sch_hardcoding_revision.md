# Ревизия hardcoding и pre-AI gates в `schedule_builder`

## Краткий вывод

`schedule_builder` сейчас является хорошим специализированным адаптером
`tNavigator 22.2 / SCHEDULE`, но не универсальным builder-ом. Основная
проблема не в наличии детерминированных проверок, а в их месте и полномочиях:
до Planner/Builder уже выбираются режим, keywords, temporal scope,
capabilities, профиль, полнота RAG и наличие schema. Агенту часто не дают
разобраться в задаче и запросить недостающие знания — workflow останавливает
её раньше.

Особенно опасно, что combat/golden-логика включается эвристиками: наличие
fact с датой автоматически означает commissioning revise, а keywords
`WELSPECS/GRUPTREE/GCONPROD/WECON` могут включить group-rebind. В результате
простая задача, немного отличающаяся от fixtures, может получить чужую
операцию, companion keywords, даты или group hierarchy.

Проверены: `n8n/workflows/core/tnavigator-schedule-builder.workflow.json`
(54 узла), генератор schedule workflow, pipeline/runtime-шаблоны, shared
Universal Orchestrator/RAG handoff и файлы
`simulation-model-example`. Прямого импорта fixture-файлов в production-коде
не найдено; coupling реализован значениями, regex и специальными ветками.
Код не менялся.

## 1. Текущий flow и количество проверок

### CREATE

```text
Normalize packet
→ packet valid?
→ deterministic intake
→ intake accepted?
→ Prepare plan
→ SCHEDULE Planner Agent
→ validate plan / plan ready?
→ SCHEDULE Builder Agent
→ validate builder
→ typed schema render
→ deterministic merge
→ validation
→ independent verifier
```

### REVISE

```text
Normalize packet
→ packet/intake gates
→ lossless baseline inventory
→ catalogue baseline decode
→ baseline semantic replay
→ baseline planning query
→ SCHEDULE Planner Agent
→ plan validation
→ targeted baseline query
→ SCHEDULE Builder Agent
→ typed IR/render
→ deterministic merge
→ commissioning/group special branch
→ candidate validation
→ independent verifier
```

В REVISE до Planner проходят примерно 12 Code-node операций и 7
условных gates; до Builder добавляются validation Planner-а и targeted query.
Это нормально для security/contract checks, но сейчас туда попали и
domain-решения. До AI уже вычисляются или требуют подтверждения:

- `CREATE/REVISE` и наличие baseline;
- simulator profile, units, policy version и даты model/history/forecast;
- полный `requested_keyword_scope` и capability/output scope;
- обязательность RAG/schema и citation coverage;
- decode/semantic boundary baseline-а;
- entity scope, temporal scope и targeted baseline selection.

Планировщик получает не исходную задачу с доступным RAG, а сильно
предобработанный и уже отфильтрованный контекст.

## 2. P0 — блокирующие проверки до AI, которые нужно убрать из generic path

### 2.1 Intake требует уже готовое инженерное решение

`n8n/templates/schedule_intake_runtime.py:16-52` блокирует workflow до
Planner-а, если отсутствуют:

- точный профиль `Rock Flow Dynamics / tNavigator / 22.2 / METRIC`;
- `petroleum-schedule-policy-v1`;
- `model_start_date`, `forecast_start`, `forecast_end`;
- history interval при `WCONHIST`;
- non-empty keyword scope, capability scope и required outputs;
- baseline и `preserve_unmentioned` для REVISE;
- approved RAG contract, catalogue, author/hash/approval и citation на каждый
  requested keyword.

Для специализированного adapter-а часть этих ограничений допустима. Но
Planner должен иметь возможность определить недостающие поля из задачи/RAG
и вернуть `needs_input`; сейчас отсутствие заранее подготовленного поля
маскируется под input error до рассуждения модели.

### 2.2 RAG используется как precondition, а не как knowledge loop

`NORMALIZE` в `schedule_pipeline.py:186-224` считает packet invalid без
валидного `rag_evidence`. Upstream
`PREPARE/ATTACH SCHEDULE RAG` (`generate_universal_engineering_workflows.py:1201-1231`)
не запускает Builder, если нет полной `keyword_instruction`, citations и
schema catalogue.

Это создаёт неверную семантику:

```text
нет знания → остановить задачу
```

вместо:

```text
Planner обнаружил нужный capability/keyword
→ targeted RAG retrieval
→ агент получил expert card + schema
→ агент запросил ещё одну карточку при необходимости
→ post-agent validation
```

Нужно оставить hard gate только для security/credential/configuration
ошибки RAG. Отсутствие конкретной карточки должно быть адресным
`knowledge_gap`/`needs_input`, а не отказом всей задачи до Planner-а.

### 2.3 До Planner выполняются typed decode и semantic validation baseline-а

REVISE заставляет baseline пройти lossless inventory, decode по approved
catalogue и semantic replay (`schedule_baseline_decoder.py`,
`schedule_semantic_runtime.py`) до первого AI-вызова.

Правильная часть — извлечение хэшей, node IDs, INCLUDE graph и наблюдаемых
фактов. Неправильная граница — отказ всей задачи, если baseline содержит
неизвестную/opaque конструкцию, которую задача не меняет. В
`schedule_baseline_decoder.py:70-75` opaque keyword превращается в error
`OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE`; decode не ограничен target scope.

Это противоречит `schedule_lossless_runtime.py:60-75`, который уже умеет
сохранить raw CST, comments, offsets и opaque nodes.

Нужная политика:

```text
unknown/opaque + не затронут → сохранить byte-for-byte, warning
unknown/opaque + затронут → targeted schema/evidence/HITL
malformed, unsafe path, size/security violation → hard error
```

### 2.4 Статический allowlist блокирует новые keywords и RAG-расширение

`generate_schedule_workflows.py:21` содержит compile-time список из 44
keywords. Он дублируется в intake, decoder, query, renderer, semantic
validator и RAG (`schedule_rag_workflows.py:15-21`).

Следствия:

- новый/vendor-specific keyword получает `UNSUPPORTED_KEYWORD` до AI;
- ingest отбрасывает custom keyword из tags (`filter(v=>allowed.has(v))`);
- retrieval не может запросить schema/card для неизвестного keyword;
- даже approved schema для нового keyword отвергается renderer-ом как
  `SCHEMA_KEYWORD_UNSUPPORTED`;
- proprietary keyword нельзя хотя бы сохранить и изменить соседний allowlisted
  block.

Allowlist должен быть не глобальным условием отказа, а частью versioned
domain profile с четырьмя состояниями:

1. `known_and_renderable`;
2. `known_but_schema_required`;
3. `unknown_but_losslessly_preservable`;
4. `unsupported_for_mutation`.

Новый keyword, покрытый RAG/schema catalogue, должен подключаться данными
profile-а, а не правкой нескольких Python templates.

### 2.5 Planner получает слишком мало исходного RAG

Shared retrieval действительно делает lexical/tag/semantic RRF и full parent
hydration (`schedule_rag_workflows.py:162-246`). Но
`PREPARE_PLAN` (`schedule_pipeline.py:287-309`) теряет существенную часть
этого результата:

- максимум 12 results;
- text/body превращается в snippet около 400 символов;
- schema catalogue превращается только в `keyword/variant/field_count`;
- parent instruction, worked example, semantics, layout и альтернативные
  варианты не доходят до Planner-а.

`PREPARE_BUILD` (`:313-330`) передаёт schema подробнее, но обычные RAG
results всё ещё обрезаются примерно до 12 карточек и 600 символов. Это не
полноценное использование RAG: Builder видит summary retrieval-а, а не
authoritative parent cards.

Нужно передавать bounded full parent cards по выбранному scope либо
immutable `knowledge_ref` с отдельным retrieval tool; нельзя молча заменять
expert card коротким snippet.

### 2.6 Нет повторного RAG retrieval после решения Planner-а

RAG scope формируется до Planner-а из `requested_keyword_scope`. Если Planner
добавил keyword/variant, capability, related keyword или required expert
card, новый retrieval не запускается. Builder остаётся со старым evidence и
может быть остановлен schema/citation gate.

Нужен ограниченный цикл:

```text
initial task retrieval
→ Planner proposal
→ targeted retrieval по proposal
→ Builder
→ knowledge gaps
→ bounded retry только по новым gaps
```

Лимиты итераций, hash evidence snapshot и idempotency оставить; менять нужно
не контроль, а направление: RAG должен помогать агенту, а не заменять его.

## 3. P0 — combat/golden assumptions, протёкшие в общий Builder

### 3.1 Любой well/date fact включает commissioning revise

`validate_builder` (`schedule_pipeline.py:362-380`) делает:

```js
useTimelineCommissioning = mode === 'REVISE' && wellFacts.length > 0
```

`wellFacts` распознаётся по aliases `Скважина/WELL/well` и
`Дата ввода/date/commissioning_date`. Не проверяется `capability_id` или
явная операция пользователя.

Затем очищается LLM `ir_events`, статус может принудительно стать
`succeeded`, а deterministic timeline переносит первый `WCONPROD` и
`WELOPEN/WEFAC`. Поэтому факты для `WCONHIST`, `WCONINJE`, `WELTARG`,
`COMPDATMD`, group dates или обычного date field могут быть ошибочно
классифицированы как commissioning.

Нужно включать эту ветку только при явном capability, например
`commissioning_date_retarget`, с typed spec:

```text
anchor_keyword, companion_keywords, identity_field, target_set,
effective_date_field, partial_source_policy, cadence_policy
```

### 3.2 Timeline runtime зашит под один lifecycle

`schedule_timeline_runtime.py:12-24, 38-41, 286-489, 595-725` фиксирует:

- authority даты ввода = первый `WCONPROD`;
- companions = только `WELOPEN` и `WEFAC`;
- изменение clock вместо изменения typed record;
- monthly 1st continuity;
- Excel-список как потенциальный полный commissioning set.

Это валидный adapter для одного combat сценария, но не общая модель
SCHEDULE. Отсутствующая строка Excel должна означать `KEEP`, пока task явно
не объявил authoritative полный set и разрешённый REMOVE scope. Сейчас
policy keep/remove существует, но сам набор и lifecycle всё равно заранее
определены под wells/commissioning.

### 3.3 New-well branch генерирует неподтверждённые записи

`applyNewWellDefinitions` (`schedule_timeline_runtime.py:350-383`) при
неполной typed definition добавляет `INCLUDE WELLTRACK`, `WELSPECS`,
`COMPDATMD`, `WELOPEN`, `WCONPROD`, `WEFAC`. Если нет строки `WCONPROD`,
используется default gas `GRAT` `100000`, а также default `OPEN`/`WEFAC`.

Это ломает injector, history-only well, completion-only add и задачу, где
control добавлять не просили. New entity должен получить только записи,
разрешённые capability и полностью подтверждённые факты; отсутствие данных
должно дать targeted gap, не default production control.

### 3.4 Group-rebind включается по keywords и угадывает параметры

`groupRevise` (`schedule_pipeline.py:365-392`) включается для REVISE без
well/date facts, если scope содержит один из
`WELSPECS/GRUPTREE/GCONPROD/WECON`.

`inferGroupRebindSpec` и `applyGroupRebindOnTimeline`
(`schedule_timeline_runtime.py:741-864`) дополнительно:

- извлекают wells из текста regex-ом по baseline `WCONPROD`;
- угадывают parent group из кавычек/слова «группа»;
- имеют fallback `DKS`;
- назначают parent-of-parent `FIELD`;
- генерируют groups `G${well}`;
- угадывают gas rate из prose/числа;
- добавляют `WELSPECS`, `GRUPTREE`, `GCONPROD GRAT`;
- re-emit-ят `WECON/WPIMULT` на первом `WCONPROD`.

В runtime есть прямая ссылка на `MAS golden_case_2`. Это должно быть отдельным
`capability_id=group_membership_rebind` и только структурированным spec:
`wells`, `parent_group`, `well_group_map`, `effective_at`, control/rate,
existing hierarchy policy. Fallback `DKS/FIELD/G${well}` и inference из prose
нужно удалить из production path.

## 4. P1 — прочий hardcoding и хрупкость

### 4.1 Upstream Orchestrator принудительно создаёт combat packet

`generate_universal_engineering_workflows.py:763-1047` содержит ещё более
опасные defaults:

- `intent: shift_commissioning_dates`;
- `source: excel:Дата ввода`;
- aliases `Wells/Скважина/Дата ввода`;
- fallback scope `DATES/WELOPEN/WCONPROD/WEFAC/INCLUDE`;
- даты `2019-06-30`, `2019-07-01`, `2071-01-01`;
- acceptance `expected: 8 wells`;
- профиль tNavigator 22.2 и petroleum controls.

Это почти прямой перенос `combat-dates-revise` fixture. Даже если Planner
выбрал другую задачу, `withReviseIntakeDefaults` и `inferScheduleKeywords`
могут подменить operation/scope. Fallback должен быть только `unknown` или
запросом уточнения; он не должен создавать engineering semantics.

### 4.2 Regex routing вместо explicit capability

`generate_universal_engineering_workflows.py:803, 868-877, 1265-1268`
маршрутизирует по словам `schedule`, `.inc`, `WCONPROD`, `WELSPECS`, Excel и
т.п. Это может сработать на комментарии, приложенном документе или задаче
аудита, где schedule только упомянут.

Нужны обязательные поля:

```text
domain_id, profile_ref, capability_id, operation,
target_scope, temporal_scope, evidence_schema, approval_policy
```

Regex может быть только suggestion для Planner, но не правом на mutation или
на выбор Excel schema.

### 4.3 Facts normalization заточен под wells и commissioning date

`PREPARE_PLAN`, `PREPARE_BUILD` и `validate_builder` повторяют fixed aliases
`Скважина/WELL` и `Дата ввода/date`. Для групп, injection, completions,
`ACTIONX`, `WELTARG` и arbitrary field updates это может дать неправильный
entity/value mapping.

Facts должны приходить как capability-defined typed rows: `entity_type`,
`entity_id_field`, `value_fields`, units, effective date, row provenance и
source snapshot. Builder не должен угадывать schema по русскому имени
колонки.

### 4.4 `INCLUDE` и virtual package

Lossless runtime правильно различает virtual `file_ref` и host filesystem и
не запрещает компонент пути с текстом `INCLUDE`. Но baseline analysis/decoder
требуют, чтобы каждый relative include был представлен в `include_files`,
точно разрешился в package graph и был расширен до decode.

Сейчас `INCLUDE_NOT_FOUND`, unsafe path, cycle или повторное расширение файла
могут заблокировать весь REVISE до Planner-а. Для production-нужд следует
разделить:

- security path violation — hard error;
- отсутствующий внешний artifact — targeted artifact gap;
- unresolved INCLUDE, который не затрагивается, — сохранить call-site и
  продолжить с opaque node;
- повторно подключённый файл — сохранить execution semantics, а не всегда
  считать ошибкой `INCLUDE_MULTIPLE_EXPANSION`.

Иначе валидный для simulator файл, который запускается только при наличии
внешнего package, ошибочно отвергается как «невалидный schedule».

### 4.5 Static emit order меняет REVISE-файл

`schedule_emit_order.py` задаёт фиксированный порядок, включая
`WELSPECS → WELLTRACK → COMPDATMD → controls`, а также имена, отсутствующие в
основном allowlist (`LGROFF`, `LGRONN`, `GSATCOMP`, `WTRACER`, `WGRUPCON` и
другие).

`schedule_timeline_runtime.py:516-571` сортирует keyword blocks внутри каждой
даты и нормализует line endings/DATES. В special path это может переставить
нетронутые blocks, comments и opaque keywords и лишить REVISE byte-preserving
семантики.

Безопасный порядок:

1. baseline order для untouched blocks;
2. explicit insertion anchors из IR;
3. profile/schema precedence для новых blocks;
4. static fallback только если profile явно его разрешил.

### 4.6 Monthly continuity — не универсальная проверка

`checkMonthlyDatesContinuity` (`schedule_timeline_runtime.py:493-513`) требует
последовательность 1-го числа месяца и выдаёт hard `MONTHLY_DATES_GAP` в
commissioning/group path. Нерегулярные, daily, quarterly и специально
разреженные schedules могут быть полностью легитимны. Cadence должен быть
частью capability/profile, а отсутствие cadence policy — вопросом, не
автоматическим отказом.

### 4.7 Профиль, policy и RAG deployment зашиты в код

По нескольким templates повторяются `tNavigator 22.2`, `METRIC`,
`petroleum-engineering`, `petroleum-schedule-policy-v1`,
`schedule_mvp`, а RAG (`schedule_rag_workflows.py:99-159, 205-246,
557-626`) жёстко использует:

- `tnavigator_schedule_knowledge_v1`;
- `tnavigator_schedule_knowledge_documents_v1`;
- `tnavigator_schedule_schema_catalogue_v1`;
- `text-embedding-3-small` и совпадающие dimensions.

Это объясняет известную ошибку `relation tnavigator_schedule_knowledge_v1
does not exist`: `Finalize indexes and deduplicate chunks` и retrieval
обращаются к vector table, тогда как bootstrap/credential/schema/model
предполагаются. Нужен явный profile/deployment preflight, который проверяет
credential, schema/table, embedding model/dimensions и возвращает
`RAG_CONFIGURATION_ERROR` до SQL finalize.

### 4.8 Несколько owners у generated surfaces

`generate_schedule_workflows.py:241` генерирует Builder, а
`generate_universal_engineering_workflows.py:2093-2105, 2342-2358` читает и
встраивает готовый JSON; отдельные RAG patch scripts меняют те же поверхности.
Ручная правка generated workflow может быть потеряна или зависеть от порядка
генераторов. Нужны один source of truth, profile manifest и capability
plugins; JSON должен быть только результатом генерации.

## 5. Что оставить до AI, а что перенести после AI

### Оставить до Planner/Builder

- JSON/packet contract, task/trace/idempotency/version identity;
- размер, encoding, NUL, unsafe absolute/escaping path и limits;
- artifact references и наличие самого root artifact;
- lossless lexical inventory, hashes, include graph и список opaque nodes;
- CAS/version guard, auth boundary, redaction и security policy;
- проверку, что выбранный `domain_id/profile_ref/capability_id` существует в
  registry (без интерпретации его деталей).

До AI эти шаги должны возвращать facts/warnings и bounded context, а не
решать, что дата — commissioning, что keyword надо менять или что весь
baseline нельзя сохранить.

### Передать Planner-у с RAG

- operation и capability interpretation;
- exact target entities/fields/variants;
- нужные keywords и dependencies;
- temporal/cadence policy, если она не задана явно;
- required Excel/source facts и column mapping;
- выбор между preserve/modify/add/remove;
- необходимость targeted RAG, schema, external INCLUDE artifact или HITL.

### Оставить после Builder

- typed IR schema/type/enum/default validation;
- target node ID + expected raw hash и optimistic merge;
- explicit REMOVE approval;
- deterministic render и package merge;
- semantic/temporal checks, preserve-unmentioned diff;
- независимый verifier и release approval.

Эти проверки должны проверять предложение агента, а не заранее подменять его.

## 6. Целевая архитектура

```text
Generic MAS core
  packet/CAS/HITL/approval/trace/routing contract

Domain adapter: tNavigator SCHEDULE
  grammar, schema registry, RAG namespace, include/package policy,
  emit precedence, simulator profile

Capability adapter
  operation, evidence schema, target policy, lifecycle rules,
  mutation/approval rules, acceptance checks

Planner + targeted RAG loop
  task → proposal → retrieval → proposal refinement → build

Builder
  typed IR only; knowledge gaps are explicit, not guessed

Post-agent deterministic pipeline
  render → lossless merge → validation → independent verification
```

Минимальный typed handoff:

```text
domain_id
profile_ref
capability_id
operation
build_mode
target_scope
temporal_scope / cadence_policy
facts_schema
requested_keyword_scope
preservation_policy
artifact_refs
acceptance_criteria
approval_policy
```

Примеры capability: `commissioning_date_retarget`,
`group_membership_rebind`, `well_control_update`, `completion_interval_add`,
`trajectory_update`, `baseline_audit`, `schedule_query`. Без явного
capability не включать timeline/group special adapter.

## 7. Приоритет исправлений

1. **P0:** убрать из Universal `withReviseIntakeDefaults` fixed operation,
   dates, `Wells/Скважина/Дата ввода`, `8 wells` и automatic profile override.
2. **P0:** убрать `wellFacts.length`/keyword-based dispatch; ввести explicit
   capability dispatch и запретить prose inference `DKS/FIELD/G${well}`.
3. **P0:** перестать блокировать REVISE на untouched opaque/unknown keyword;
   preserve его raw bytes, schema требовать только при target mutation.
4. **P0:** сделать RAG iterative: initial retrieval → Planner proposal →
   targeted retrieval → Builder knowledge gaps/retry; передавать full parent
   cards и полный touched schema.
5. **P0:** вынести allowlist/schema/emit order/lifecycle rules в один
   versioned profile manifest; дать custom keyword режим
   `preserve/needs_schema`.
6. **P1:** сделать facts schema и Excel mapping capability-defined;
   различать partial source и authoritative full set.
7. **P1:** разделить include security, missing artifact и opaque preservation;
   не требовать полного decode нетронутого external include.
8. **P1:** отключить monthly check по умолчанию и запретить default
   `GRAT 100000`/автоматические companion keywords.
9. **P1:** добавить RAG/database preflight до ingest/retrieval SQL.
10. **P2:** оставить один generator owner и добавить registry-driven adapters.

## 8. Regression matrix

- combat commissioning revise — только regression fixture, не default;
- golden group rebind с явным typed spec и с не-`DKS` parent;
- REVISE с неизвестным untouched keyword — byte-preserving success;
- REVISE с unknown keyword в target — targeted schema/HITL, не ранний общий
  отказ;
- custom keyword, для которого schema/instruction пришли из RAG;
- `WCONHIST`, `WCONINJE`, `WELTARG`, `ACTIONX`, `COMPDATMD` без commissioning
  facts;
- producer, injector и completion-only new entity без лишних автоматических
  keywords/default rate;
- partial Excel target set versus explicit authoritative full set + approved
  REMOVE;
- daily/quarterly/irregular/non-monthly DATES;
- nested INCLUDE, path component `INCLUDE`, отсутствующий внешний artifact,
  duplicate shared include и include cycle;
- comments, CRLF, opaque blocks и exact no-op REVISE;
- Planner расширяет keyword scope — выполняется targeted RAG retrieval;
- отсутствует schema variant или есть ambiguous variants;
- missing/wrong vector table, credential, embedding model/dimensions — ясный
  preflight error;
- новый simulator/profile и non-petroleum task — не попадают в petroleum
  schedule adapter по regex.

## Итог

Правильное направление — не убрать deterministic validation и не разрешить
LLM писать произвольный `.INC`. Нужно сделать deterministic слой
capability-aware и post-agent-oriented: до AI оставить только generic
contract/security/lossless facts, а domain interpretation, keyword discovery и
RAG lookup отдать Planner/Builder. Typed IR, content hashes, lossless merge и
independent verifier следует сохранить — это сильная основа для гибкого
Schedule Builder после удаления silent combat/golden dispatch.
