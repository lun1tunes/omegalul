# Ревизия hardcoding в Orchestrator / MAS

## Краткий вывод

Система уже имеет хороший переиспользуемый control plane: CAS/version guard, HITL-gates, allowlist специалистов, bounded packets, artifact refs, trace и независимую проверку. Но поверх него production-ветка SCHEDULE фактически реализует сценарии `combat-dates-revise` и `golden_case_2`, а не универсальный orchestration contract.

Критический дефект не в наличии petroleum/tNavigator-адаптера как такового, а в том, что его поведение включается эвристиками (`есть Excel facts`, `есть WELSPECS/GRUPTREE`, совпало слово в тексте) и получает боевые значения по умолчанию. Поэтому простая задача, немного отличающаяся от combat/golden, может быть классифицирована как сдвиг дат ввода или group rebind и получить чужие записи/поля.

Проверены: generated universal orchestrator (95 узлов), SCHEDULE Builder (54 узла), их генераторы и runtime-модули, Excel adapter, registry/instruction template, RAG ingestion/retrieval и fixtures в `simulation-model-example`. Код не менялся.

## P0 — прямой hardcoding под combat/golden, который нужно убрать из production path

### 1. `APPLY_PLAN` генерирует combat-запрос даже при отсутствии спецификации

Источник: `n8n/templates/generate_universal_engineering_workflows.py:820-840, 867-887`.

- fallback `requested_change_scope` = `intent: shift_commissioning_dates`, `source: excel:Дата ввода`, keywords `DATES/WELOPEN/WCONPROD`;
- fallback даты: `2019-06-30`, `2019-07-01`, `2071-01-01`;
- fallback scope: `DATES/WELOPEN/WCONPROD/WEFAC/INCLUDE`;
- профиль безусловно заменяется на `Rock Flow Dynamics / tNavigator / 22.2 / METRIC`;
- Excel packet принудительно требует лист `Wells`, колонки `Скважина` и `Дата ввода`;
- acceptance criterion содержит `expected: 8 wells`;
- текст/objective прямо описывает сдвиг `first WCONPROD` и сохранение `GRAT/WEFAC/INCLUDE`.

Это почти буквальная имплементация `simulation-model-example/combat-dates-revise/TASK.md:7-18` и его восьми строк (`:23-31`). Для задачи с другим листом, другими колонками, другим числом объектов, без Excel или с другим типом изменения такой packet неверен.

### 2. Любые well/date facts автоматически становятся commissioning revise

Источники: `schedule_pipeline.py:365-380`, `schedule_timeline_runtime.py:869-974`.

`mode === REVISE && wellFacts.length > 0` включает timeline path. Не проверяется явный `operation/capability_id` или то, что пользователь действительно просил менять commissioning date. Дата из произвольного fact (`date`, `value`, `Дата ввода`, `date`) трактуется как ввод скважины.

Дальше deterministic path заменяет LLM IR и переносит первый `WCONPROD` плюс первый `WELOPEN/WEFAC`. Так будут неверно обработаны, например, факты для `WCONHIST`, `WCONINJE`, `WELTARG`, `COMPDATMD`, `ACTIONX`, group dates или обычная дата в иной операции.

### 3. Timeline semantics зашиты под один lifecycle

Источник: `schedule_timeline_runtime.py:12-24, 38-41, 286-489`.

Ввод скважины всегда определяется как первый `WCONPROD`; перемещаются только `WELOPEN` и `WEFAC`; удаление unlisted затрагивает только этот набор. Это не универсальная timeline-модель, а `commissioning_date_adapter` для конкретного сценария. Нужны lifecycle rules из capability/profile: anchor keyword, companion keywords, identity field, period, conflict policy и mutation scope.

Дополнительные опасные предположения:

- `removeUnlistedCommissioning` может удалить baseline records всех скважин, которых нет в Excel, если policy=`remove`; отсутствие строки в источнике не должно означать REMOVE без явного target scope;
- `checkMonthlyDatesContinuity` (`:493-513`) требует месячную последовательность 1-го числа и может отвергнуть легитимный нерегулярный schedule;
- baseline wells определяются по `MOVE_KEYWORDS`, а не по явному target set.

### 4. New-well branch выдумывает структуру и gas control

Источник: `schedule_timeline_runtime.py:350-383, 574-583`.

При наличии `new_well_defs` код автоматически добавляет `INCLUDE WELLTRACK`, `WELSPECS`, `COMPDATMD`, `WELOPEN`, `WCONPROD`, `WEFAC`. Если `wconprod_line` не задан, генерируется `GRAT` с default `100000`; если не задана строка, добавляются также фиксированные `OPEN`/`WEFAC` шаблоны. HITL заранее требует именно trajectory, `WELSPECS`, `COMPDATMD` и стартовый gas rate.

Для новой injection-well, history-only well, well без WEFAC или задачи, где добавление control не просили, это fabrication. CREATE/ADD должен эмитить только явно разрешённые capability records и только после полного typed evidence.

### 5. `group_rebind` из golden case 2 протёк в общий production flow

Источники: `schedule_timeline_runtime.py:741-864`, `:909-931`; `schedule_pipeline.py:329, 381-392`.

- `inferGroupRebindSpec()` извлекает wells из любого текста по совпадению с baseline `WCONPROD`;
- parent group угадывается из кавычек/слова «группа»;
- остаётся fallback `DKS`;
- parent-of-parent безусловно `FIELD`;
- группы по умолчанию получают имена `G${well}`;
- rate угадывается regex-ами из prose/числа;
- `applyGroupRebindOnTimeline()` добавляет `WELSPECS`, `GRUPTREE`, `GCONPROD GRAT` и повторно эмитит `WECON/WPIMULT` на первом `WCONPROD` date;
- комментарий прямо ссылается на `MAS golden_case_2`.

Это соответствует `simulation-model-example/golden-cases/golden_case_2/Описание задачи.txt:1` (1601/1602, DKS, 200 тыс. м³/сут). Обычная задача, в тексте которой случайно встречаются существующие well id, число и слово «группа», может получить чужую иерархию и control. `group_rebind` должен запускаться только по явному capability и структурированному spec (`wells`, `parent_group`, `well_group_map`, `control`, `rate`, `effective_at`, approval policy); fallback-инференс из прозы нужно удалить.

Отдельно: `groupRevise = REVISE && !wellFacts && scope.some(WELSPECS/GRUPTREE/GCONPROD/WECON)` (`schedule_pipeline.py:365-392`) означает, что одного keyword scope достаточно для включения этой специальной мутации.

## P1 — эвристики, делающие маршрутизацию хрупкой

### 6. Regex-классификация вместо capability registry

Источники: `generate_universal_engineering_workflows.py:802, 867-876, 1203-1264`; `generate_schedule_workflows.py:145-181`.

Задача направляется в SCHEDULE по словам/регуляркам `schedule`, `.inc`, `WCONPROD`, `WELSPECS`, `GRUPTREE`, `tNavigator`, Excel и т.п. `inferScheduleKeywords()` всегда добавляет `DATES`, а слова про группу могут автоматически добавить `WELSPECS`, `GRUPTREE`, `WECON`, `GCONPROD`. Наличие facts или keywords подменяет решение Planner-а специальной deterministic branch.

Проблемы:

- слово в комментарии, приложенном документе или baseline может изменить маршрут;
- упоминание SCHEDULE в задаче о диагностике не равно просьбе его изменить;
- keyword scope не определяет operation (audit, query, modify, add, remove, rebind);
- `requested_keyword_scope` местами используется как доказательство наличия entity/temporal scope.

Нужен явный `domain_id`, `capability_id`, `operation`, `target_scope` и `evidence_schema`. Regex допустим только как предложение маршрута, но не как право на mutation.

### 7. Excel extraction слишком узок и повторяется в нескольких слоях

Фиксированные `Wells/Скважина/Дата ввода` находятся в universal orchestrator и повторных Excel handoff-ах (`generate_universal_engineering_workflows.py:884-887, 978-982`), а также отражены в Builder-инструкциях и normalization.

Нужно передавать `sheet_selector`, `column_mapping`, `entity_type`, `value_fields`, `row_filters`, `date/units policy` из task capability. Для generic Excel task возможны другие листы/колонки/объекты/несколько таблиц или отсутствие workbook. Excel specialist уже умеет принимать `required_columns/requested_fields`; orchestrator не должен подменять их combat-профилем.

### 8. Deterministic SCHEDULE pipeline насильно выбирает две специальные ветки

`schedule_pipeline.py:364-407`:

- при well/date facts очищает `ir_events`, автоматически считает timeline path и выставляет `status=succeeded`;
- без таких facts, но с четырьмя keywords, очищает IR и включает group rebind;
- затем merge/timeline runtime фактически заменяет обычный typed IR.

Это должно быть capability-dispatch, например `capability_id=commissioning_date_retarget` или `capability_id=group_membership_rebind`. Для любой другой capability deterministic layer обязан пропустить общий IR renderer, а не угадывать специальную операцию.

### 9. Слишком широкое удаление/сохранение по Excel-снимку

`runCommissioningRevise()` сравнивает множество baseline commissioning wells со всеми Excel identities (`schedule_timeline_runtime.py:595-683`). Это корректно только для явно объявленной операции «синхронизировать полный список commissioning». Для частичного target set отсутствие строк должно означать KEEP, если task не объявил полный authoritative set и разрешённый REMOVE scope.

## P1 — domain hardcoding, допустимый только внутри profile/adapter

### 10. Universal control plane жёстко связан с petroleum profile

Источники: `generate_universal_engineering_workflows.py:661-675, 801, 806-807`; `orchestrator-instruction.template.md:31-41, 77-94`; `schedule_intake_runtime.py:27-52`; `schedule_schema_runtime.py:31`; `schedule_baseline_decoder.py:30`.

В общем orchestrator/instruction и в SCHEDULE intake зашиты:

- `access_scope=petroleum-engineering`;
- `Rock Flow Dynamics / tNavigator / 22.2`;
- `METRIC`;
- `petroleum-schedule-policy-v1`;
- обязательные SCHEDULE profile/schema/RAG checks.

Это может быть корректным ограничением текущего SCHEDULE adapter, но не универсальной MAS системой. Для другой инженерной области такой task должен либо ошибочно получить petroleum packet, либо застрять на `PROFILE_NOT_APPROVED`. Профиль должен приходить как versioned domain adapter binding; generic orchestrator должен знать только contract и capability.

### 11. tNavigator keyword allowlist, emit order и lifecycle-грамматика находятся в universal-facing surface

`generate_schedule_workflows.py:21, 70-181`, `schedule_rag_workflows.py:15-21`, `schedule_emit_order.py`, schema/semantic runtimes содержат фиксированный allowlist и правила порядка/семантики tNavigator. Для специализированного `tnavigator-schedule-builder` это нормально; для компонента с названием `universal-engineering-orchestrator` — нет. Также любой новый keyword/capability требует правок нескольких Python templates и генерации JSON.

Domain adapter должен получать allowlist, schema catalogue, emit order и lifecycle rules как одну версионируемую конфигурацию, а generic core — только валидировать общий packet.

### 12. Обязательные assumptions в Builder instruction

`schedule_pipeline.py:100-175` фиксирует preferred path `WELSPECS → WELLTRACK → COMPDATMD → WCONHIST/WCONPROD/WCONINJE`, FIELD/group semantics, tNavigator-specific limitations и ряд out-of-scope keywords. Это полезный policy для конкретного adapter, но его нельзя применять к простой задаче с другим simulator/profile или к calculation/document specialist. Особенно опасно, что инструкция содержит специальные указания «commissioning dates» и «group/GCONPROD/WELSPECS rebind» как готовые deterministic сценарии.

## P1 — RAG и deployment hardcoding

### 13. Физические таблицы и модель embeddings зашиты в workflow

Источники: `schedule_rag_workflows.py:23-27, 99-159, 182-205, 557-626`; `apply_mas_hybrid_rag.py:107-127, 309, 358-365`.

Зашиты:

- vector table `tnavigator_schedule_knowledge_v1`;
- parent table `tnavigator_schedule_knowledge_documents_v1`;
- schema table `tnavigator_schedule_schema_catalogue_v1`;
- namespaces `schedule_mvp`, `excel_protocol`, `orchestrator_routing`, `specialist_template`;
- `text-embedding-3-small` и requirement одинаковых dimensions;
- SQL check `simulator_version='22.2'` и default `target_base='schedule_mvp'`.

Это не combat-specific, но это жёсткая привязка deployment/domain к одной БД и одному corpus. Она также объясняет известную ошибку `relation tnavigator_schedule_knowledge_v1 does not exist`: `Finalize indexes and deduplicate chunks` обращается к vector table и создаёт только parent/schema tables; существование vector table, правильный PostgreSQL credential, schema и возможность PGVector node создать таблицу предполагаются. Table name, schema, embedding model/dimensions и bootstrap должны быть одной явной runtime-конфигурацией с preflight check, а не строками в нескольких templates.

### 14. Namespace routing не является расширяемой registry-only конфигурацией

Добавление нового domain/corpus требует одновременно менять `NAMESPACES_JS`, SQL, filters, metadata, RAG gates, ingestion examples и workflow generator. `target_base` должен быть allowlisted через versioned capability/domain registry; unknown namespace должен давать ясный configuration error, а не silently fallback в `schedule_mvp` или `petroleum-engineering`.

## P2 — источники истины и поддерживаемость

### 15. Несколько генераторов владеют одними surface

`generate_universal_engineering_workflows.py:2092-2105, 2340-2369` читает готовый `tnavigator-schedule-builder.workflow.json`, тогда как `generate_schedule_workflows.py:241` также генерирует этот workflow. `apply_mas_hybrid_rag.py` ещё патчит generated JSON и universal orchestrator.

Итого, ручная правка generated workflow может быть потеряна при следующей генерации, а поведение может зависеть от порядка запуска генераторов. Нужен один source of truth: generic generator + profile manifest + capability modules; generated JSON только регенерируется и не редактируется вручную.

### 16. Registry специалиста частично статичен

`n8n/contracts/specialist_registry.v1.json:27-177` хорошо фиксирует specialist IDs и capabilities, но schedule capability уже описана как `tNavigator 22.2 SCHEDULE`, а route numbers/call nodes/switch outputs зашиты в generator. Для добавления нового универсального специалиста одного registry недостаточно: требуется кодовый switch, adapter и generated workflow. Это следует явно оформить как plugin/adapter registration и fail-closed diagnostic.

## Что оставить как есть

Не являются проблемой сами по себе: CAS, optimistic versioning, idempotency keys, explicit HITL/approval, bounded artifact refs, redacted trace, independent verifier, unknown-specialist fail-closed и test fixtures в `simulation-model-example`. Combat/golden fixtures могут оставаться regression tests. Проблема — наличие их assumptions в production runtime, а не сами fixtures.

Поиск не выявил production-импорта файлов из `simulation-model-example`; coupling реализован через значения/ветки и комментарии, а не через прямой filesystem dependency. Прямые остатки fixture-driven логики: `8 wells`, fallback dates, `DKS`, `FIELD`, `G${well}`, `MAS golden_case_2` и комментарий `combat REVISE`.

## Рекомендуемая декомпозиция

```text
Generic MAS core
  task lifecycle / CAS / HITL / approval / routing contract / trace / verifier

Domain adapter (selected by domain_id + profile_ref)
  tNavigator SCHEDULE: grammar, schema catalogue, RAG namespace, emit order,
  lifecycle rules, package materializer, domain-specific specialist

Task capability (explicit capability_id)
  operation, target entities, facts schema, keyword scope, temporal policy,
  mutation policy, required evidence, acceptance checks, approval requirements
```

Минимальный typed handoff для SCHEDULE должен содержать как минимум:

`domain_id`, `profile_ref`, `capability_id`, `operation`, `build_mode`, `target_scope`, `effective_at/temporal_scope`, `facts_schema`, `requested_keyword_scope`, `preservation_policy`, `artifact_refs`, `acceptance_criteria`.

Примеры capability IDs: `commissioning_date_retarget`, `group_membership_rebind`, `well_control_update`, `completion_interval_add`, `trajectory_intersection`, `baseline_audit`. Только capability `commissioning_date_retarget` может разрешать WCONPROD/WELOPEN/WEFAC anchor rules; только `group_membership_rebind` — group tree/control changes. Нет capability — нет deterministic mutation; запросить уточнение.

## Приоритет исправлений

1. **P0:** удалить из universal `APPLY_PLAN` fixed dates/profile/sheet/columns/`8 wells`; запретить defaults, которые меняют operation или scope.
2. **P0:** заменить `wellFacts.length` и keyword regex на explicit capability dispatch; отключить prose `inferGroupRebindSpec()` и fallback DKS/FIELD/G-prefix.
3. **P0:** запретить new-well fabrication/default GRAT; принимать только typed definitions, согласованные capability.
4. **P0:** вынести `WCONPROD` commissioning adapter из generic pipeline; сделать lifecycle/companion rules конфигурацией.
5. **P1:** сделать Excel sheet/column mapping и partial-vs-authoritative source scope явными.
6. **P1:** вынести profile, policy version, allowlist, schema/emit rules и RAG namespaces в domain adapter manifest.
7. **P1:** добавить RAG preflight: одинаковый credential/schema/table/model/dimensions; отдельно создать/проверить vector table до `Finalize`.
8. **P2:** оставить один генератор-владелец каждого workflow и добавить registry-driven plugin registration.

## Минимальная матрица регрессии после исправлений

- commissioning-date revise: Excel `Wells/Скважина/Дата ввода` — fixture-only;
- тот же revise с другим листом/колонками и с частичным target set;
- text-only `WCONHIST`, `WCONINJE`, `WELTARG`, `ACTIONX`, `COMPDATMD`;
- group rebind с не-`DKS` group, существующей иерархией и без gas control;
- new producer, injector и completion-only add без лишних автоматически добавленных keywords;
- irregular/non-monthly dates;
- CREATE без baseline и REVISE без Excel;
- non-petroleum specialist task и новый RAG namespace;
- отсутствующая/wrong PostgreSQL vector table — понятный preflight error до SQL finalize.

**Итог:** текущая реализация хорошо подходит как специализированный `tNavigator SCHEDULE / combat-compatible adapter`, но ещё не является гибким универсальным MAS. Главная граница исправления — не «убрать все petroleum keywords», а не позволять domain-specific capability и fixture assumptions silently управлять generic orchestrator.
