# Ревизия hardcoding и pre-AI gates в `Excel Extractor Agent`

## Краткий вывод

`Excel Extractor Agent` уже имеет сильный безопасный фундамент: изолированные
сессии, bounded tool API, детерминированные query/export, opaque IDs и
серверную валидацию результата. Но в интеграционном production path он ещё не
является универсальным extractor-ом. Поверх generic Excel tools протекли
assumptions `tNavigator / SCHEDULE / commissioning dates`: ожидаются лист
`Wells`, колонки `Скважина` и `Дата ввода`, наличие identity скважины/группы и
последующий handoff в Schedule Builder.

Главная архитектурная проблема — не сами проверки, а их граница. До запуска
AI Agent уже выполняются protocol-RAG gate, table matching, выбор/ограничение
таблицы и часть semantic validation. После Agent generic результат может быть
отклонён только потому, что в нём нет well/group identity, строк или
provenance, хотя для обычной задачи это может быть корректно. Нужны отдельные
generic Excel capability и явный domain/capability adapter для SCHEDULE.

Проверены: native workflow
`n8n/workflows/core/excel-extraction-agent.workflow.json` (63 узла),
`excel-engineering-specialist-adapter.workflow.json` (8 узлов), universal
orchestrator (95 узлов), `excel-agent-tools/app/*`, генераторы workflow и RAG,
операционный guide, контракты `source-facts-packet`/`specialist-result`, Excel
fixtures и smoke-тесты. Код не менялся.

## 1. Текущий flow и граница до AI

Основной путь выглядит примерно так:

```text
upload или continuation
→ auth / type / size / session gates
→ Excel protocol RAG retrieval и ready-gate
→ deterministic match_tables / detect_tables
→ ambiguity clarification и selected table
→ Excel Extractor AI Agent с bounded tools
→ save_agent_plan → query_table
→ deterministic validation / finalization / export
→ specialist-result adapter
→ optional Excel → SCHEDULE handoff
```

До Agent уже принимаются решения о:

- наличии и достаточности `excel_protocol` knowledge;
- том, на каких листах искать таблицу;
- том, какая таблица считается selected и какие колонки являются
  `suggested_select`;
- необходимости остановить задачу из-за ambiguity;
- допустимости результата по количеству строк, полям и provenance.

Безопасность файла, auth, session isolation, opaque IDs и настоящая
неоднозначность должны оставаться hard gates. Выбор бизнес-смысла таблицы,
семантика колонок, фильтры и допустимость пустого результата не должны
безусловно решаться deterministic preflight до Planner/Agent.

## 2. P0 — coupling с combat/SCHEDULE вместо generic Excel contract

### 2.1 Universal orchestrator создаёт combat-пакет по умолчанию

`n8n/templates/generate_universal_engineering_workflows.py:823-840`
(`withReviseIntakeDefaults`) при отсутствии полной спецификации добавляет:

- `intent: shift_commissioning_dates`;
- source `excel:Дата ввода`;
- keywords `DATES/WELOPEN/WCONPROD` и затем companion scope
  `WEFAC/INCLUDE`;
- профиль `Rock Flow Dynamics / tNavigator / 22.2 / METRIC`;
- даты `2019-06-30`, `2019-07-01`, `2071-01-01`.

Это уже не default extraction, а выдумывание operation, domain, temporal
scope и mutation policy. Значения фактически повторяют combat fixture.
Отсутствие поля должно приводить к `needs_input`/планированию, а не к
commissioning revise.

### 2.2 Excel packet жёстко требует `Wells/Скважина/Дата ввода/8 wells`

При workbook и baseline orchestrator принудительно создаёт packet:

- sheet hint `Wells`;
- exact columns `Скважина` и `Дата ввода`;
- objective про дату ввода объекта SCHEDULE;
- acceptance criterion `expected: 8 wells`.

Повторный extraction после evidence gap снова требует `Скважина`/`Группа` и
`Дата ввода` (`:963-1009`). Поэтому таблица цен, KPI, справочник, lookup,
текстовый отчёт или другая инженерная таблица может быть отвергнута ещё до
полезного вызова агента.

Эти требования должны приходить из typed capability packet:
`sheet_selector`, `column_mapping`, `entity_type`, `value_fields`, filters и
acceptance policy. `Wells`, русские названия и число 8 должны остаться только
в regression fixture для отдельной capability.

### 2.3 Успешный Excel result автоматически превращается в Schedule facts

В universal handoff (`generate_universal_engineering_workflows.py:1280-1292`)
результат при признаках schedule направляется как `source_facts_packet` в
Schedule Builder. Маршрутизация и `schedule_requested` частично опираются на
regex/текстовые признаки (`schedule`, `WCONPROD`, `.inc`, `tNavigator` и
подобные), а не на явные `consumer`/`capability_id`.

В результате Excel-only extraction может неожиданно стать частью mutation
pipeline. Handoff должен быть явным:

```text
Excel result_kind=tabular_extract
→ consumer=none | schedule_builder
→ capability_id=commissioning_date_retarget
```

Наличие даты, well-like column или слова SCHEDULE не является разрешением на
передачу фактов в Builder.

## 3. P0 — проверки до AI Agent, которые забирают у него смысловую работу

### 3.1 Обязательный protocol RAG — слишком сильный precondition

В native workflow и universal orchestrator отсутствующий полный
`excel_protocol` приводит к тому, что Agent вообще не запускается
(`EXCEL_PROTOCOL_RAG_REQUIRED`). Это смешивает две разные ситуации:

```text
RAG/configuration service недоступен → hard configuration error
не найдена конкретная knowledge card → targeted knowledge_gap/needs_input
```

Для generic extraction достаточно базовой policy/security card; профильная
инструкция должна подбираться по capability и при необходимости запрашиваться
после решения Agent. Нельзя блокировать любой Excel task из-за отсутствия
одной protocol card.

### 3.2 `match_tables` до Agent фактически выбирает таблицу

Цепочка `Preflight match tables` → `Assess deterministic clarification need`
может:

- самостоятельно вызвать `detect_tables`;
- выбрать `selected_table` по score;
- сформировать `suggested_select`;
- остановить execution на ambiguous sheets/tables.

Это полезный bounded discovery, но сейчас deterministic matcher частично
заменяет Planner: модель получает не workbook hypothesis space, а уже
отфильтрованную таблицу. Минимальная безопасная граница:

- оставить file/session/security checks;
- оставить hard clarification, когда две таблицы действительно
  неразличимы;
- передавать Agent ranked candidates, score и объяснение выбора;
- считать automatic selection advisory, если пользователь не указал
  однозначный `table_selector`.

### 3.3 Native Agent принимает только один workbook binary

Входной контракт workflow ориентирован на binary workbook и continuation
session. Нет универсального пути для:

- CSV/ODS и других допустимых tabular artifacts;
- remote artifact/query result по opaque reference;
- уже подготовленного dataset/dataframe;
- нескольких workbook в одном запросе.

При этом FastAPI tool layer имеет собственную политику форматов (`.xlsx`,
`.xlsm`, `.xltx`, `.xltm`, `.xls`), что создаёт рассинхронизацию между native
workflow и service contract. Входной `input_kind` и capability должны быть
явными, а не выводиться из наличия binary.

### 3.4 Query repair может переписать решение пользователя

После Agent workflow проверяет saved plan и при «лишних» колонках может
повторить запрос с deterministic `suggested_select`. Это опасно для запроса
«верни все колонки» или для намеренно широкого результата: сервис принимает
эвристику выбора полей за исправление плана агента. Repair должен выполняться
только при нарушении явного output schema/size policy и с сохранением
исходного плана в trace.

## 4. P0 — Excel result contract ошибочно специализирован под SCHEDULE

### 4.1 Hard blockers требуют well/group identity и непустые строки

`ADAPT_EXCEL_RESULT` в adapter/workflow normalizer выставляет blocker-ы:

- `EXCEL_REQUESTED_FIELDS_MISSING`;
- `EXCEL_NO_FACT_ROWS`;
- `EXCEL_PROVENANCE_REQUIRED`;
- `EXCEL_SOURCE_CONFLICT`;
- `EXCEL_SELF_CHECK_FAILED`;
- `EXCEL_ENTITY_IDENTITY_MISSING`;
- `EXCEL_REQUIRED_COLUMNS_INCOMPLETE`.

При этом identity распознаётся в основном по aliases `Скважина/WELL` и
`Группа/GROUP`. Таким образом, extraction цен, KPI, дат без entity, агрегата,
справочника или обычной lookup-таблицы может быть переведён в `needs_input`,
хотя результат полностью соответствует запросу.

Нужно разделить:

```text
generic tabular_result
schedule source_facts_packet
```

Well/group identity, даты и exact row shape должны быть обязательны только
для capability, которая их объявила.

### 4.2 Пустой result всегда считается ошибкой

Для `success/partial` отсутствие `row_count/returned_count` превращается в
`EXCEL_NO_FACT_ROWS`. Но пустой результат может быть корректным: фильтр не
нашёл строк, пользователь запросил отсутствующие значения или lookup
действительно пуст. Нужны отдельные состояния:

- `expected_empty` — результат валиден;
- `no_match` — фильтр валиден, совпадений нет;
- `table_not_found`/`schema_mismatch` — требуется уточнение;
- `partial/truncated` — результат неполный и должен явно это сообщить;
- `execution_error` — реальная ошибка.

`empty_result_policy` должен быть частью packet, а не универсальным blocker-ом.

### 4.3 Проверка required columns чувствительна к регистру

Доступность полей нормализуется через lower-case set, но проверка
`required_columns` в строках результата использует exact keys. `Well`, `WELL`,
`well` или локализованное отображение могут дать ложный
`EXCEL_REQUIRED_COLUMNS_INCOMPLETE`. Нужны canonical column IDs и отдельные
display labels; exact spelling допустима только по explicit schema policy.

### 4.4 Provenance и snapshot недостаточно сильны для reproducibility

Terminal provenance в основном содержит `table_id/sheet/range`, adapter
добавляет индекс строки, а `source_snapshot_hash` строится по compact
preview/metadata, не по полному immutable workbook/result snapshot. Для
повторной проверки этого недостаточно при одинаковом preview и разных
остальных строках, формульных значениях или фильтрах.

Нужен typed row/cell provenance минимум с:
`workbook_hash`, artifact revision, sheet, physical row/cell range,
header-path, canonical field, formula/value mode, applied filters и
query/result hash. Полный snapshot должен быть immutable и отделён от
ephemeral `result_id`/artifact ID.

## 5. P1 — эвристики в `excel-agent-tools`

`excel-agent-tools/app/excel_tools.py` полезен как bounded deterministic layer,
но часть правил зашита под ограниченный набор public/messy fixtures:

1. Листы `Description`, `Notes`, `Readme`, `AFOSHEET`, `Changelog` и
   `Index` распознаются по фиксированным именам и заранее исключаются или
   понижаются.
2. Total/subtotal, units и date/period columns распознаются фиксированными
   английско-русскими regex/токенами.
3. Sheet suffix aliases `p/m/w` автоматически трактуются как
   people/men/women.
4. Matcher использует фиксированные stop-words, prefix matching, score
   thresholds и gaps; другие языки, морфология, transliteration и доменные
   naming conventions не покрыты.
5. Hidden sheet получает score `-1000` и практически исключается из
   natural-language matching, даже если пользователь явно просит данные из
   hidden листа. Прямой explicit sheet selector должен иметь приоритет.

Сами heuristics допустимы как discovery hints, но не как доказательство
семантики. Их следует вынести в locale/domain profile и возвращать вместе с
explanation/confidence; при близких scores — показывать candidates, а не
принудительно выбирать один.

### 5.1 Формулы, `.xlsm` и macro policy

Workbook читается через `openpyxl` с `data_only=True`. Формула без cached
value может превратиться в `None`, а policy для formula text, recalculation,
macro preservation и наличия VBA metadata не отражена в result contract.
`.xlsm` принимается, но это не означает, что его formula/macro semantics
сохранены. Нужно явно возвращать `formula_policy`/`macro_metadata` и выбирать:
cached value, formula text, recalculation unavailable или HITL.

### 5.2 Значения `n/a`, `null`, ellipsis не всегда пустые

`_EMPTY_PLACEHOLDERS` превращает `n/a`, `null`, `…`, `...` и `⋯` в `None`.
В корпоративных данных `NA`/`N/A` может быть кодом категории, буквальным
значением или допустимым status. Политика empty tokens должна приходить из
profile/capability и сохранять исходное значение в provenance.

### 5.3 Duplicate columns и multi-table layouts

Дубликаты заголовков получают suffix `(2)`, а merged/multi-row headers и
side-by-side regions реконструируются эвристически. Это хороший fallback, но
canonical mapping должен учитывать физический column ID/header path, иначе
две одинаково названные колонки могут быть перепутаны. Bounds и caps
(`preview`, `max_tables`, row limits) нужно отражать как `truncated/partial`,
а не молча выдавать неполный dataset.

## 6. P1 — RAG и deployment coupling

### 6.1 Protocol RAG зашит под petroleum namespace

`generate_universal_engineering_workflows.py`, `apply_mas_hybrid_rag.py` и
operating guide используют `access_scope=petroleum-engineering`, фиксированные
tags/topics и `top_k` (в отдельных путях 8). Retrieval почти не зависит от
решённой capability и не делает targeted loop после решения Agent.

Правильная граница, уже частично отражённая в guide: RAG объясняет protocol и
tool policy, но не подменяет workbook facts. Её нужно сохранить и добавить:

```text
initial task retrieval
→ Agent proposes operation/table/fields
→ targeted capability/locale retrieval
→ bounded retry по knowledge_gap
```

Отсутствие карточки должно быть адресным gap, а недоступность RAG service,
credential, embedding или schema — диагностируемой configuration error.

### 6.2 Общие RAG tables и embedding model — deployment hardcoding

Shared RAG templates зашивают:

- `tnavigator_schedule_knowledge_v1`;
- `tnavigator_schedule_knowledge_documents_v1`;
- `tnavigator_schedule_schema_catalogue_v1`;
- `text-embedding-3-small` и совместимые dimensions;
- context-seeder default `n8n_excel_agent_context`.

Excel Agent наследует эту coupling даже когда ему не нужен SCHEDULE corpus.
Это также источник ошибки вида `relation ... does not exist`: workflow
предполагает существование vector table, правильного schema/credential,
embedding model и dimensions. Нужен единый deployment manifest и startup
preflight, проверяющий vector/parent/schema tables, credential, embedding
dimension и namespace до запуска ingest/retrieval/finalize.

### 6.3 Native workflow имеет UI-only deployment hardcoding

В workflow присутствуют placeholders/имена, которые нельзя считать
runtime configuration:

- `REPLACE_WITH_REACHABLE_FASTAPI_HOST:8000/api/v1`;
- `REPLACE_WITH_FASTAPI_API_KEY`;
- `REPLACE_WITH_WEBHOOK_API_KEY`;
- `n8n_excel_agent_chat_memory`;
- фиксированные имена OpenAI/Postgres credentials и workflow IDs.

Это не domain-логика, но частая причина «агент не видит сервис» и дрейфа между
UI/imported JSON и env. Endpoint, key, memory table, model, timeout и
credential refs должны задаваться одной deployment-конфигурацией; generated
workflow должен fail fast с понятным missing-config сообщением.

## 7. P1 — adapter и handoff

`excel-engineering-specialist-adapter` корректно защищает specialist packet,
continuation и correlation, но его `ADAPT_EXCEL_RESULT` фактически знает о
следующем Schedule consumer. В универсальном контракте результат должен
содержать `result_kind`, `schema_ref`, `artifact_refs`, `provenance` и
`consumer_handoffs[]`; `source_facts_packet` — отдельный typed projection,
создаваемый только при явной capability.

Минимальная разделённая модель:

```text
Excel Extractor
  → tabular_result / query_result / empty_result / partial_result

Schedule adapter
  → source_facts_packet только для commissioning_date_retarget
```

Excel-only task должен завершаться после generic verification/export и не
проходить Schedule RAG, Schedule retry loop или commissioning identity gate.
Excel → Schedule fixture следует оставить отдельным end-to-end capability
test, а не default path.

## 8. Что уже хорошо и нужно сохранить

- mandatory `X-API-Key`, auth boundary и fail-closed runtime configuration;
- ограничения размера файла, ZIP-bomb/archive checks и поддерживаемые suffixes;
- session locking, TTL cleanup, path traversal protection и atomic state save;
- opaque `tbl_`/`res_`/`art_`/`clr_` IDs;
- workbook и значения ячеек считаются untrusted data; prompt injection не
  должен исполняться как инструкция;
- Agent получает bounded tool results, а не весь workbook;
- `save_agent_plan → query_table` и deterministic finalization/export;
- explicit ambiguity clarification, bounded previews/results и защита от
  duplicate requests;
- CSV formula-injection neutralization и существующие complex-layout,
  retrieval и contract smoke-tests.

Нужно не убрать deterministic слой целиком, а перестать использовать его
эвристики как silent business decision.

## 9. Целевая декомпозиция

```text
Generic Excel capability
  input artifact/session, inspect, table candidates, query, export,
  typed provenance, empty/partial policy, reproducibility

Domain/profile adapter
  locale, header aliases, units, formula policy, visibility policy,
  RAG namespace, schema catalogue

Task capability
  operation, target_scope, table_selector, column_mapping, filters,
  output_schema, acceptance criteria, consumer handoff

SCHEDULE capability (отдельный plugin)
  commissioning_date_retarget, entity identity, date policy,
  source_facts_packet и explicit Schedule Builder handoff
```

Минимальный typed request должен включать:
`input_kind`, `operation`, `target_scope`, `table_selector`,
`requested_fields/column_mapping`, `filters`, `empty_result_policy`,
`partial_result_policy`, `provenance_level`, `formula_policy`,
`visibility_policy`, `locale`, `output_schema` и `consumer`.

Рекомендуемый flow:

```text
security/session preflight
→ lightweight workbook introspection
→ Planner/Agent + bounded RAG proposes candidates and query
→ deterministic query/validation/export
→ clarification only for hard ambiguity or missing required evidence
→ generic typed result
→ explicit consumer-specific projection
```

## 10. Приоритет исправлений

1. **P0:** удалить из universal Excel path fixed intent, profile, dates,
   `Wells`, `Скважина`, `Дата ввода` и `8 wells`; отсутствие спецификации не
   должно включать commissioning revise.
2. **P0:** заменить well/group/date blockers и automatic `source_facts_packet`
   на explicit `capability_id`, `result_kind` и `consumer`.
3. **P0:** ослабить `EXCEL_NO_FACT_ROWS`, identity и provenance gates через
   `empty_result_policy`/`provenance_level`; исправить case-sensitive
   required-column validation.
4. **P0:** оставить pre-AI только security, session и настоящую ambiguity;
   передавать Agent ranked candidates вместо безусловного semantic selection.
5. **P1:** вынести matcher rules, empty tokens, hidden/formula/macro policy,
   locale и thresholds в versioned profiles; сделать `.xlsm` semantics
   явными.
6. **P1:** добавить CSV/ODS/remote artifact/multi-workbook input contract и
   typed row/cell provenance с immutable snapshot.
7. **P1:** externalize endpoint/keys/credentials/memory/RAG tables/model и
   добавить RAG/vector preflight с понятным configuration diagnostic.
8. **P2:** сделать capability/consumer registry единственным источником
   handoff и оставить один generator-owner для каждого generated workflow.

## 11. Обязательная regression matrix

- другие sheet names и языки колонок;
- несколько таблиц и side-by-side tables на одном листе;
- merged/multi-row headers;
- hidden sheet, выбранный явным selector-ом;
- generic extraction без wells/groups/dates;
- ожидаемый пустой результат;
- partial/truncated result и корректный `partial` status;
- duplicate columns и canonical header paths;
- formula cells, `.xlsm`, cached values и macro metadata policy;
- буквальные `NA`, `n/a`, `null`, ellipsis;
- несколько workbook и remote/query-result artifact input;
- prompt injection в cell values, file name и sheet names;
- provenance, row/cell ranges и воспроизводимость snapshot;
- Excel-only task без Schedule handoff;
- Excel → Schedule commissioning fixture как отдельная explicit capability;
- отсутствующая vector table, неверный RAG credential и embedding mismatch;
- отсутствие RAG card: targeted `knowledge_gap`, а не безусловный отказ всей
  задачи.

**Итог:** текущий компонент хорошо подходит как управляемый Excel/SCHEDULE
adapter и безопасный bounded extraction service, но generic Agent слишком
часто получает заранее принятое domain-решение. Сначала нужно отделить
generic tabular extraction от `commissioning_date_retarget`, затем оставить
RAG и deterministic tools помощниками с typed evidence, а не блокирующими
заменами Planner/Agent.
