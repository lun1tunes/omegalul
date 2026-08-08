# Petroleum Engineering MAS: исследование и roadmap

**Целевая платформа:** n8n **2.30.8**.  
**Дата ревизии:** 2026-08-08.  
**Статус документа:** целевая архитектура и план; перечисленные как `planned` компоненты еще не являются частью runtime.

## 1. Решение в одном абзаце

Строим не «рой свободно разговаривающих агентов», а управляемую систему **orchestrator–specialists**. n8n хранит состояние задачи, применяет policy/HITL, вызывает только статически разрешенные workflow и собирает результат. LLM планирует и формирует черновики, но не выбирает workflow ID, не исполняет произвольный код и не утверждает собственный инженерный артефакт. Для `.DATA`/`SCHEDULE` действует конвейер `plan -> evidence -> draft -> deterministic parse/lint -> independent review -> human approval -> release`. Excel Extractor остается готовым отдельным specialist. Детерминированные вычисления выполняет будущий Math Service, а знания извлекаются отдельным hybrid-RAG workflow из версионированной PGVector/PostgreSQL базы.

## 2. Что показало исследование

### 2.1. MAS и agentic workflows

- Начинать следует с минимальной композиции и добавлять автономность только там, где фиксированный workflow недостаточен. Для инженерной системы это означает **детерминированный control plane и ограниченные model-driven workers**, а не бесконтрольные handoff между агентами.
- Полезные паттерны: prompt chaining с программными gates, routing, parallel sectioning, orchestrator–workers и evaluator–optimizer. Они применяются по необходимости, а не все одновременно.
- Долгие задачи требуют durable state, возможности продолжить после ошибки/HITL и отделения task state от conversational memory.
- HITL должен быть policy-driven: approve/reject/edit для контролируемых действий, сохраненное состояние и новая авторизованная команда продолжения. Зависший execution не является надежным task ledger.
- Dynamic discovery допустим для **поиска кандидатов**, но authority всегда остается детерминированной: зарегистрированный logical ID, статический n8n binding, allowlist, схема входа/выхода, timeout и risk policy.
- Генератор и verifier разделяются. Self-check полезен, но не независим. Критический результат не выпускается без ответственного человека.

Это соответствует уже выбранному направлению Universal Orchestrator: модель формирует структурированный план, а Code/Switch/Execute Sub-workflow контролируют переходы.

### 2.2. Ограничения n8n 2.30.8

- Границей specialist служат `Execute Sub-workflow Trigger` и `Execute Sub-workflow`; связь после импорта выбирается в UI, потому что workflow ID инсталляционно-зависим.
- AI Agent получает ограниченные штатные tools, но не прямой доступ к оркестраторскому state store или произвольному workflow.
- Data Table пригодна для первой версии task ledger. Для большого production-контура понадобится политика retention/backup/audit и, вероятно, отдельные PostgreSQL таблицы событий; Chat Memory не заменяет durable task state.
- UI-only поставка не может зависеть от `$env`, Global Variables, shell или серверных файлов. Runtime-конфигурация и credentials выбираются в UI; переносимые знания встраиваются в ingestion JSON либо передаются через Form/Webhook.
- Нативная PGVector node обеспечивает vector retrieval, но сама по себе не реализует полноценное объединение semantic + PostgreSQL full-text + tags. Для hybrid retrieval нужна SQL-ветка/отдельный retrieval workflow и rank fusion.
- Для совместимости фиксируем только registry IDs/typeVersion, подтвержденные официальным `n8nio/n8n:2.30.8`, и после каждого изменения выполняем чистый CLI import этим образом.

### 2.3. Нефтегазовый инженерный контур

LLM нельзя считать парсером или валидатором deck. Любой артефакт должен иметь:

- immutable source references, SHA-256, revision, author/approver и provenance;
- явные simulator family/dialect/version: ECLIPSE 100/300, tNavigator profile и т. п.;
- unit system (`METRIC` only), coordinate reference/orientation и sign conventions;
- проверенную структуру секций: `RUNSPEC`, `GRID`, `EDIT`, `PROPS`, `REGIONS`, `SOLUTION`, `SUMMARY`, `SCHEDULE`;
- безопасный разбор `INCLUDE` graph: canonical path, запрет traversal/cycles, allowlisted roots, depth/size limits, hashes;
- keyword/card grammar, required/forbidden combinations, dimensions/counts, defaulted values и dialect compatibility;
- сеточные/региональные/PVT/SCAL/rock/transmissibility проверки, где они применимы;
- для SCHEDULE: монотонные даты/timesteps, wells/groups/completions, control mode, constraints, units, restart compatibility и конфликтующие controls;
- deterministic parser/linter report с severity и точной ссылкой на файл/keyword/record;
- semantic engineering review допущений и согласованности после синтаксической проверки;
- diff относительно approved baseline и обязательный release gate.

Для формата и regression oracle предпочтительны официально разрешенные руководства симулятора и open-source OPM parser/Flow. Документы поставщика не копируются в RAG целиком без прав: хранятся разрешенные фрагменты, ссылки, версия и hash.

## 3. Целевая архитектура

```text
HTTP / Form / Execute Sub-workflow
                 |
                 v
Universal Petroleum Engineering Orchestrator
  |- intake validation + identity/risk policy
  |- durable task ledger + optimistic concurrency + event references
  |- planner (structured plan only)
  |- capability catalogue (searchable) + binding allowlist (authoritative)
  |- deterministic router
  |- retry / replan / compensation / HITL
  |- independent verifier
  `- release gate + audit result
                 |
     +-----------+----------------------+-------------------+
     |                                  |                   |
Excel Specialist (implemented)   RAG Researcher       Math Specialist
  -> Excel FastAPI tools          -> Hybrid RAG         -> discover schema
                                                        -> exact execution
     |
Future bounded specialists
  |- Reservoir DATA Deck Builder
  |- Forecast SCHEDULE Builder
  |- Deck Parser/Linter
  |- Simulator Compatibility Reviewer
  `- Simulator Job Adapter (submit/poll/cancel; later)
```

### 3.1. Неподвижные архитектурные инварианты

1. **Один control plane.** Specialists не меняют authoritative task state.
2. **Logical IDs only.** LLM видит `specialist_id`/capabilities, но не workflow ID или credentials.
3. **Contract first.** Все specialists принимают `specialist_packet` и возвращают `specialist_result` с версией схемы.
4. **Artifacts by reference.** В state/prompt только компактный JSON; модели, decks, grids и отчеты — immutable artifact refs.
5. **At-least-once safe.** Каждая команда имеет task/version/idempotency key; side effects либо идемпотентны, либо имеют compensation/manual resolution.
6. **Fail closed.** Неизвестный route/schema/dialect/units/revision переводит задачу в clarification/decision, а не угадывается.
7. **Evidence is untrusted input.** RAG/Excel content не может переопределять system policy и не является разрешением на tool call.
8. **No self-release.** Builder, deterministic validator, independent verifier и approver — разные роли.
9. **Reproducibility.** Сохраняются prompt/model/tool/function/knowledge versions, normalized inputs и artifact hashes.
10. **Bounded execution.** Ограничены retries, model/tool calls, payload, timeout, parallelism и стоимость.

### 3.2. Capability registry: discovery без опасной динамики

Разделяем две сущности:

- **catalogue** — searchable metadata: logical ID, description, input/output contract, tags, owner, risk, version, health, examples;
- **bindings** — статически настроенные Execute Sub-workflow nodes и allowlist в оркестраторе.

Planner может найти кандидатов по описанию/тегам. Policy node проверяет разрешенный `specialist_id`, contract/risk/version и переводит его в заранее подключенную ветку. Модель никогда не передает URL или workflow ID. Добавление агента = импорт template, UI binding, registry record, allowlist enable и contract/eval tests.

## 4. Шаблоны specialists

| Шаблон | Назначение | Обязательные gates |
|---|---|---|
| Reasoning specialist | Ограниченный анализ/план | structured output, sources/assumptions, no side effects |
| RAG researcher | Ищет инженерные основания | access filters, citations, coverage/abstain |
| Tool specialist | Вызывает детерминированный API | discovery then exact allowlisted call, schemas, timeout/idempotency |
| Artifact builder | Создает draft deck/SCHEDULE/report | immutable draft, parser/linter, diff; никогда не publish |
| Deterministic validator | Синтаксис, schema, units, constraints | machine-readable findings, no LLM verdict |
| Independent verifier | Проверяет evidence и engineering consistency | не изменяет draft, отдельная модель/роль |
| HITL decision | Clarification/approval/rejection | authenticated actor, reason, version/gate ID |
| Long-running adapter | Simulator/тяжелый job | submit/status/cancel/resume, durable job ID |
| Excel adapter | Табличные входы | уже реализован через universal boundary |

## 5. Production-grade hybrid RAG на PostgreSQL + PGVector

### 5.1. Почему текущего PGVector workflow недостаточно

Текущий `excel-rag-ingestion.workflow.json` — корректный переносимый operating context только для Excel Agent. Он не является MAS-wide knowledge base: нет lexical rank, governed metadata/version approval, dedup/update и evaluation. Его сохраняем, но не расширяем до универсальной базы неуправляемым добавлением документов.

### 5.2. Планируемая модель данных

```text
knowledge_documents
  document_id, title, source_uri, source_hash, revision,
  authority_level, owner, confidentiality_scope,
  simulator, simulator_version, dialect, unit_system,
  effective_from/to, status(draft|approved|obsolete), metadata_jsonb

knowledge_chunks
  chunk_id, document_id, ordinal, parent_path, content,
  content_hash, tags[], deck_section, keyword,
  search_tsvector, embedding, embedding_model, embedding_dimensions,
  created_at

ingestion_runs
  run_id, source, status, counts, errors, initiated_by, timestamps

knowledge_eval_cases / knowledge_eval_runs
  query, required_filters, relevant_chunk_ids, forbidden_chunk_ids, metrics
```

Для изменения embedding model/dimensions создается новая версия индекса/таблицы и выполняется controlled cutover; смешивать размерности нельзя.

### 5.3. Ingestion workflow

`engineering-knowledge-ingestion.workflow.json` должен иметь три входа: Form upload, authenticated HTTP и Execute Sub-workflow. Этапы:

1. аутентификация/размер/access metadata;
2. malware/content-type check вне LLM, если доступен корпоративный scanner;
3. parse + structure-aware normalization;
4. SHA-256 dedupe и document revision;
5. обязательная metadata/schema validation;
6. section-aware parent/child chunking, overlap только там, где нужен контекст;
7. embeddings одной зарегистрированной моделью;
8. `tsvector`, tags и exact keyword fields;
9. запись в staging/draft;
10. retrieval preview + validation/eval;
11. human publish approval;
12. atomic status switch, audit и rollback.

Повторный ingestion одного hash идемпотентен. Удаление означает obsolete/tombstone, а не потерю audit trail.

### 5.4. Retrieval workflow

`engineering-hybrid-retrieval.workflow.json`:

1. нормализует запрос, caller scope, simulator/dialect/version, section, tags и effective date;
2. исключает неapproved/obsolete/чужой scope **до** ранжирования;
3. параллельно получает semantic candidates (`pgvector`) и lexical/exact candidates (PostgreSQL FTS/keyword);
4. объединяет независимые rankings через **Reciprocal Rank Fusion**, а не складывает несопоставимые raw scores;
5. дедуплицирует и диверсифицирует по document/section;
6. опционально rerank ограниченного top-N;
7. собирает parent context в заданном token budget;
8. возвращает snippets, citations, revisions, scores, filters и coverage;
9. abstains/запрашивает уточнение при слабом покрытии или конфликтующих authoritative sources.

Результат RAG — evidence packet. Цитаты обязательны; точный keyword lookup имеет отдельный boost, а approved/current/authority задаются policy, не prompt-инъекцией.

### 5.5. RAG quality gates

- retrieval: Recall@K, MRR/nDCG, exact-keyword accuracy, filter leakage = 0;
- generation: citation correctness/completeness, groundedness, abstention quality;
- operations: duplicate rate, stale-hit rate, latency p50/p95, cost, failed ingestion;
- adversarial: instruction injection in documents, access-scope leakage, obsolete/manual conflict, mixed dialect/version;
- evaluation corpus versioned by domain experts; publish/cutover блокируется при regression.

## 6. Math Service

Python-сервис выполняет только зарегистрированные детерминированные функции; оркестрация и выбор остаются в n8n.

```text
GET  /health
GET  /api/v1/functions?query=&tags=&version=
GET  /api/v1/functions/{name}/{version}
POST /api/v1/functions/{name}/{version}/execute
POST /api/v1/functions/batch
```

Descriptor содержит canonical `name`, semantic version, description/tags, JSON input/output schemas, units/dimensions, precision, determinism, side effects, limits и examples. Поток: exact/tag discovery -> при необходимости semantic candidate search -> policy allowlist -> fetch schema -> validate -> execute exact name+version -> validate output -> audit.

Запрещены arbitrary Python, `eval/exec`, пользовательский source code, shell, filesystem/network. Нужны API auth, request/idempotency ID, timeout/memory/input limits, finite-number checks, explicit units, stable rounding policy и property/golden tests. Первые функции: unit conversion, polygon/polyline geometry, cell bulk volume, pore volume, volumetric aggregation и schedule date arithmetic — только после утверждения инженерных формул/конвенций.

## 7. Нефтегазовые artifact workflows

### 7.1. DATA Deck Builder

- принимает approved inputs/refs и simulator profile;
- строит typed intermediate representation (IR), а не свободный текст;
- renderer детерминированно превращает IR в deck;
- parser повторно читает результат (round-trip where possible);
- linter проверяет section/keyword/dimensions/includes/units;
- compatibility reviewer отмечает неподдерживаемые/неоднозначные keywords;
- independent engineering verifier проверяет assumptions/provenance;
- человек видит report и semantic diff до release.

### 7.2. Forecast SCHEDULE Builder

Отдельный bounded specialist, потому что прогнозные controls имеют иной risk/validation lifecycle. Вход — approved base model/restart, forecast assumptions, wells/groups, events/constraints, dates и units. Выход — draft SCHEDULE + machine-readable event IR + validation report. Проверяются chronology, duplicate/conflicting events, completion intervals/status, control modes/limits, group hierarchy, dialect и restart date. Только Release workflow создает approved artifact.

### 7.3. Simulator integration (после builders)

Не запускать симулятор синхронным tool call. Adapter должен иметь `submit/status/cancel/result`, sandbox/work directory, allowlisted executable/profile, resource quota, immutable input manifest и job ID. Dry-run/parser check предшествует полноценному run. Логи/summary сохраняются как artifacts, secrets и host paths не возвращаются модели.

## 8. Ревизия текущего репозитория

| Компонент | Сейчас | Решение |
|---|---|---|
| Universal Engineering Orchestrator | реализован: state/CAS/HITL/router/verifier | база control plane; сделать petroleum profiles и registry |
| Excel Extraction Agent + adapter | реализован и подключен логически | сохранить; обновлять только universal contract/integration |
| Excel FastAPI tools | реализован отдельно, Windows/Docker | не смешивать с MAS control plane |
| Engineering specialist template | реализован базовый | расширить семейством шаблонов из раздела 4 |
| Excel RAG ingestion | semantic operating guide | оставить specialist-local; не считать MAS hybrid RAG |
| MAS hybrid RAG | отсутствует | ingestion/retrieval/approval/eval workflows + SQL schema |
| Math Service | отсутствует | API registry + n8n discovery specialist |
| DATA/SCHEDULE builders/validators | отсутствуют | проектировать после knowledge/eval foundation |
| Capability registry | статический catalogue/allowlist в Code | вынести searchable catalogue, сохранить static bindings |
| Observability/evals | локальные contract tests | добавить event/audit/evaluation workflows и dashboards |
| `excel-mas-orchestrator` | legacy | не импортировать в greenfield; удалить после migration window |

## 9. План реализации и Definition of Done

### Phase 0 — contracts, threat model, petroleum profiles

**Работы:** versioned packet/result/artifact/evidence/error schemas; roles/risk matrix; simulator-profile schema; units/provenance; idempotency/event model; prompt-injection and access threat model.  
**DoD:** JSON fixtures good/bad; fail-closed contract tests; architecture decision records; no runtime behavior based on free-form status.

### Phase 1 — orchestrator v2 + reusable templates

**Работы:** petroleum catalogue; capability registry/search; deterministic bindings; cost/time/tool budgets; standard error taxonomy; agent/tool/RAG/builder/verifier/HITL/job templates; audit event emission.  
**DoD:** unknown/deconfigured route cannot execute; resume/CAS/retry/approval/adversarial tests; Excel path remains green; UI setup count documented.

### Phase 2 — governed hybrid RAG

**Работы:** PostgreSQL migrations; UI-callable ingestion, retrieval, publish and evaluation workflows; RRF; metadata/access filters; citations/abstention; seed pack as text JSON.  
**DoD:** gold retrieval set passes agreed thresholds; no scope/status leakage; duplicate ingestion idempotent; embedding cutover and rollback tested; all workflows import cleanly in 2.30.8.

### Phase 3 — Math Service + discovery specialist

**Работы:** FastAPI template/registry; initial approved functions; schema/units/limits; n8n specialist; contract/property/golden/security tests; Windows `.bat` and Docker.  
**DoD:** discovery never equals authorization; arbitrary code impossible; exact function/version and inputs recorded; concurrent/batch/idempotency tests pass.

### Phase 4 — deterministic deck foundation

**Работы:** typed deck/SCHEDULE IR; simulator profiles; INCLUDE graph; parser/linter; artifact manifest/diff; OPM-based test oracle where compatible.  
**DoD:** golden valid/invalid Eclipse/tNavigator cases; round-trip/dialect/units/include/security tests; no LLM required for validation.

### Phase 5 — DATA and SCHEDULE specialists

**Работы:** RAG researcher, DATA builder, SCHEDULE builder, compatibility reviewer, independent verifier, release approval workflow.  
**DoD:** end-to-end cases generate only `draft` until deterministic and human gates pass; evidence/citations/versions/hashes complete; unsupported dialect abstains.

### Phase 6 — simulator adapter and production hardening

**Работы:** async job lifecycle; sandbox/quota; monitoring; backup/restore; retention; RBAC/SSO/API gateway; secrets rotation; red-team/evals; runbooks.  
**DoD:** failure/restart/cancel/replay/load/DR exercises; audit reconstruction; operational SLOs; signed release checklist.

## 10. Smoke gate после каждого изменения workflow

Автоматический gate:

1. parse всех JSON и проверка schema/unique node names/connections/no orphans;
2. allowlist registry IDs + exact `typeVersion` для n8n 2.30.8;
3. compile JavaScript всех Code nodes;
4. contract/security tests, отсутствие `$env`/Global Variables и секретов;
5. все delivery workflows `active:false`, placeholders не могут открыть webhook;
6. чистый CLI import **в новую пустую n8n DB** официальным `n8nio/n8n:2.30.8`;
7. повторный export/import round-trip, если формат менялся;
8. `pytest` FastAPI/integration fixtures и `git diff --check`.

Ручной UI gate в чистом test project:

1. импортировать только файлы из manifest в указанном порядке;
2. создать credentials/Data Table и выполнить все UI bindings из checklist;
3. убедиться, что ни одна runtime node не красная;
4. прогнать HTTP, Form и Execute Sub-workflow entrypoints;
5. проверить clarification, stale version, unauthorized call, retry, reject/approve, Excel upload и RAG citations;
6. активировать только после успешного результата и сохранить sanitized import report.

CLI import подтверждает формат/registry, но не доказывает доступность корпоративных credentials, сети, прав PGVector и ручных workflow bindings — это отдельный обязательный UI smoke.

## 11. Источники и примененные выводы

Доступ проверен 2026-08-08. Версии n8n-нод фиксируются дополнительно по официальному Docker image, поскольку online docs могут описывать более новую release.

- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): простые composable patterns; routing, orchestrator-workers, evaluator-optimizer; complexity only when needed.
- [LangGraph — Persistence](https://docs.langchain.com/oss/python/langgraph/persistence): checkpoint/store separation, resume, fault tolerance и HITL.
- [LangChain — Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop): policy-driven approve/edit/reject и durable pause/resume pattern.
- [n8n — Execute Sub-workflow](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflow/): явные sub-workflow inputs и UI database binding.
- [n8n — AI Agent](https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/): штатный agent/tool composition; фактические node versions сверяются с 2.30.8 image.
- [Microsoft — Hybrid search](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview): parallel full-text/vector retrieval и Reciprocal Rank Fusion.
- [pgvector](https://github.com/pgvector/pgvector): exact/approximate vector search в PostgreSQL, индексы и эксплуатационные свойства Postgres.
- [PostgreSQL — Text Search](https://www.postgresql.org/docs/current/textsearch.html): lexical index, dictionaries, ranking и query operators.
- [Pinecone — Retrieval-Augmented Generation](https://www.pinecone.io/learn/retrieval-augmented-generation/): grounding domain/current knowledge и базовый retrieval/generation lifecycle.
- [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework): govern/map/measure/manage как основа risk/evaluation lifecycle.
- [OWASP GenAI Security — Agentic threats and mitigations](https://genai.owasp.org/resource/agentic-ai-threats-and-mitigations/): tool misuse, excessive agency, prompt-injection и security boundaries.
- [OPM common/parser](https://github.com/OPM/opm-common) и [OPM Flow/simulators](https://github.com/OPM/opm-simulators): open-source Eclipse deck keyword/parser и regression reference; совместимость с конкретным simulator profile должна проверяться отдельно.
- SLB ECLIPSE и tNavigator manuals: использовать только лицензированные организацией версии как authoritative knowledge sources; не включать защищенный контент в репозиторий без разрешения.

## 12. Решения, которые нужно согласовать до Phase 2–4

1. Корпоративная PostgreSQL role: разрешены ли SQL Query nodes, миграции, FTS indexes и отдельные schemas.
2. Artifact storage и retention: S3/MinIO/SharePoint/корпоративное DMS, допустимые размеры и immutable policy.
3. Поддерживаемая первая матрица simulator/version/dialect и юридически доступные manuals.
4. Identity/RBAC для Form/Webhook approvals и knowledge access scopes.
5. Корпоративные chat/embedding/reranker models, dimensions, data residency и budgets.
6. Где будет развернут Math Service и разрешен ли n8n к нему network route.
7. Может ли test contour запускать OPM Flow или vendor simulator для dry-run/regression.
8. Инженеры-владельцы gold cases и лица, имеющие release authority.
