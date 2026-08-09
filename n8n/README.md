# n8n 2.30.8 — UI-only установка Petroleum Engineering MAS

Эта поставка рассчитана строго на n8n `2.30.8`. Доступ к серверной файловой системе, shell, Global Variables и `$env` не нужен.

## Архитектура MVP

`Universal Engineering Orchestrator` — единственный stateful control-plane. Он хранит состояние задачи в Data Table, управляет CAS-version, HITL, retry/replan, вызывает специалистов по allowlist и отправляет результат независимому Verifier.

SCHEDULE runtime:

```text
task + optional baseline .data/.inc text
→ keyword/scope planning
→ Hybrid Retrieval: lexical + semantic + exact tags + RRF
→ SCHEDULE Builder: CREATE или preserve-by-default REVISE
→ deterministic renderer/merge/validator
→ Independent Verifier
→ optional result approval
→ { filename: "schedule.inc", schedule_text: "..." }
```

Excel Extractor вызывается только через Orchestrator. Если Builder возвращает `evidence_gap`, Orchestrator сохраняет состояние, запрашивает недостающие табличные данные через Excel Adapter и возобновляет тот же Builder. Прямого вызова Excel из Builder нет.

Calculation Specialist также вызывается только через Orchestrator. Его Adapter передаёт все приложенные `.dev` и одну ASCII CPS3 поверхность одним batch-запросом в простой Math Service, получает JSON с пересечением для каждого исходного имени файла и при SCHEDULE-задаче возвращает управление Planner для следующего делегирования Builder.

## Импорт через UI

Канонический manifest: [`import-manifest.json`](import-manifest.json).

Минимальный runtime-порядок:

1. `workflows/calculation-specialist-adapter.workflow.json`;
2. `workflows/excel-extraction-agent.workflow.json`;
3. `workflows/excel-engineering-specialist-adapter.workflow.json`;
4. `workflows/tnavigator-schedule-knowledge-ingestion.workflow.json`;
5. `workflows/tnavigator-schedule-hybrid-retrieval.workflow.json`;
6. `workflows/tnavigator-schedule-builder.workflow.json`;
7. `workflows/mas-trace-event-writer.workflow.json`;
8. `workflows/universal-engineering-orchestrator.workflow.json`.

Диагностические/переиспользуемые SCHEDULE foundation workflows также перечислены в `full_clean_import_set`. Они позволяют отдельно проверять intake, baseline, renderer, merge, validator, verifier и release.

Не активируйте:

- `excel-mas-orchestrator.workflow.json` — legacy migration-only;
- `engineering-specialist-template.workflow.json` — шаблон нового специалиста;
- `ai-components.workflow.json` — справочный canvas;
- `excel-extraction-form-adapter.workflow.json` — нужен только для отдельной Excel-формы.

Все workflow экспортированы с `active:false`.

## Шесть обязательных bindings

В UI выберите импортированные target workflows в следующих Execute Workflow nodes:

| Owner | Node | Target |
|---|---|---|
| Universal Orchestrator | `Call Excel Extraction Specialist Adapter` | Excel Adapter |
| Universal Orchestrator | `Call SCHEDULE Hybrid Retrieval` | SCHEDULE Hybrid Retrieval |
| Universal Orchestrator | `Call SCHEDULE Builder Specialist` | SCHEDULE Builder |
| Universal Orchestrator | `Call Calculation Specialist` | Calculation Adapter |
| Universal Orchestrator | `Call MAS Trace Event Writer` | MAS Trace Writer |
| Excel Adapter | `Call native Excel Extraction Agent` | Excel Extraction Agent |

Calculation route уже включён и требует импортированного Adapter. Data/Document routes остаются optional extension points; не переводите их в `configured:true`, пока соответствующие workflow не импортированы и не проверены.

В `Math Service Configuration` адаптера задайте URL без использования `$env`/`$vars`: по умолчанию `http://127.0.0.1:8100/api/v1/math`. Если n8n работает на сервере, `127.0.0.1` указывает на сервер n8n, а не на ваш Windows-PC. Сам сервис запускается по [`../fastapi-math-service/README.md`](../fastapi-math-service/README.md) и в MVP не требует credential.

## Data Table состояния

Создайте `engineering_orchestrator_tasks_v1`:

| Column | Type |
|---|---|
| `task_id` | String |
| `version` | Number |
| `status` | String |
| `phase` | String |
| `task_type` | String |
| `risk_class` | String |
| `request_json` | String |
| `context_json` | String |
| `plan_json` | String |
| `specialist_json` | String |
| `result_json` | String |
| `verification_json` | String |
| `pending_human_json` | String |
| `last_error_json` | String |
| `retry_count` | Number |
| `max_retries` | Number |
| `history_json` | String |
| `created_at` | String |
| `updated_at` | String |

Во всех state Data Table nodes выберите эту одну таблицу.

Создайте `mas_trace_events_v1`:

| Column | Type |
|---|---|
| `event_id` | String |
| `trace_id` | String |
| `task_id` | String |
| `at` | String |
| `stage` | String |
| `event_type` | String |
| `actor` | String |
| `status` | String |
| `summary` | String |
| `details_json` | String |

Выберите её в `MAS Trace Event Writer`.

## Credentials

Настройте через UI:

- OpenAI/OpenAI-compatible chat model для Planner, Builder и Verifier; для текущих задач используйте дешёвые модели с temperature `0`;
- embedding model для SCHEDULE ingestion и retrieval;
- PostgreSQL credential для memory, lexical tables и PGVector;
- Header Auth для входного webhook Orchestrator;
- Header Auth/API key для Excel FastAPI.

Модель и размерность embeddings при записи и чтении должны совпадать. При смене модели используйте новую PGVector collection/table.

## SCHEDULE Knowledge Ingestion

`tnavigator-schedule-knowledge-ingestion` имеет Form и Execute Sub-workflow входы. Он принимает один экспертный блок за запуск:

```json
{
  "schedule_knowledge_block": {
    "contract": "schedule_knowledge_block",
    "contract_version": "1.0",
    "target_base": "schedule_mvp",
    "knowledge_type": "keyword_instruction",
    "knowledge_id": "wconprod-forecast-v1",
    "revision": "1",
    "title": "WCONPROD — прогнозный контроль скважины",
    "keywords": ["WCONPROD"],
    "topics": ["Контроль по скважинам", "Прогноз"],
    "task_patterns": ["задать ограничение по воде"],
    "status": "active",
    "author": "expert-name",
    "access_scope": "petroleum-engineering",
    "text": "Полная экспертная инструкция и ограничения..."
  }
}
```

Допустимые типы:

- `keyword_instruction` — полное описание keyword и условий применения;
- `worked_example` — задача, корректный SCHEDULE fragment и пояснение.

Optional `schema_catalogue_json` используется для точного deterministic render: порядок полей, required/default/enum/type и семантические правила. Это экспертный JSON-справочник, а не внешний deployment dependency.

Ingestion пишет полный parent document в PostgreSQL и chunks в PGVector. Hybrid Retrieval выполняет:

1. PostgreSQL lexical search;
2. PGVector semantic search;
3. exact keyword/topic/tag search;
4. deterministic RRF;
5. фильтрацию `target_base=schedule_mvp`, access scope и active revision;
6. hydration полного parent document.

Если для requested keyword нет активной полной инструкции, система abstain/fail closed и формирует конкретный HITL-вопрос.

## Работа с `.data/.inc`

Form Orchestrator принимает файл как binary и преобразует его в UTF-8 text штатной `Extract From File` node. Текст передаётся Builder внутри execution context. Для `REVISE` он является baseline; незатронутые блоки, comments и unknown-but-preserved sections сохраняются lossless, если задача явно не требует удаления или концептуальной замены.

На выходе после проверки и approval:

```json
{
  "release": {
    "contract": "schedule_release_result",
    "contract_version": "1.0",
    "status": "approved",
    "filename": "schedule.inc",
    "schedule_text": "DATES\n  1 JAN 2027 /\n/\n..."
  }
}
```

## Human-in-the-Loop

Workflow не держит долгий Wait execution. При уточнении он сохраняет `awaiting_human` и возвращает `task_id`, `version`, `gate_id`, причину и конкретные вопросы.

Ответ приходит новым вызовом:

```json
{
  "action": "reply",
  "task_id": "eng_...",
  "expected_version": 3,
  "gate_id": "gate_eng_...",
  "requested_by": "engineer",
  "human_response": {
    "answers": [{"question_id": "oil_rate", "answer": "120 m3/day"}]
  }
}
```

Поддерживаются `status`, `reply`, `approve`, `reject`, `retry`, `cancel`. Неверный `gate_id` или stale `expected_version` завершается conflict без изменения состояния.

## Три входа Orchestrator

- authenticated HTTP Webhook;
- n8n Form Trigger;
- Execute Sub-workflow Trigger.

Стартовая форма использует `n8n-nodes-base.formTrigger`. `n8n-nodes-base.form` — отдельная нода страницы формы/результата и не заменяет trigger.

## Совместимость n8n 2.30.8

Критические registry IDs официального image:

- AI Agent: `@n8n/n8n-nodes-langchain.agent`, typeVersion `3.1`;
- HTTP Request Tool: `@n8n/n8n-nodes-langchain.toolHttpRequest`, typeVersion `1.1`;
- Form Trigger: `n8n-nodes-base.formTrigger`, typeVersion `2.6`;
- n8n Form: `n8n-nodes-base.form`, typeVersion `2.5`.

Если корпоративная сборка показывает неизвестную ноду, экспортируйте из неё минимальный workflow с одной рабочей нодой. UI-label не всегда совпадает с registry ID в JSON.

## Excel Agent

В `Runtime configuration` задайте:

| Поле | Значение |
|---|---|
| `excel_tools_url` | сетевой URL FastAPI, включая `/api/v1` |
| `excel_tools_api_key` | `API_KEY` из `excel-tools.env` |
| `excel_webhook_api_key` | отдельный секрет входного Excel webhook |

FastAPI можно поднять на Windows только через CMD: [`../excel-agent-tools/README.md`](../excel-agent-tools/README.md).

## Smoke перед активацией

1. Импортировать все файлы из `full_clean_import_set` в пустую n8n `2.30.8` и убедиться, что они остаются `active:false`.
2. Проверить отсутствие красных unknown-node/credential warnings в runtime-наборе.
3. Настроить шесть bindings, обе Data Tables и URL Math Service.
4. Загрузить экспертную инструкцию и получить её через lexical, semantic и exact-tag branches.
5. Прогнать `CREATE` без baseline.
6. Прогнать `REVISE` с `.data/.inc`; проверить preservation и diff.
7. Вызвать `evidence_gap`; проверить Excel extraction, сохранение state и resume Builder.
8. Передать `.dev` + ASCII CPS3 в Calculation Adapter и проверить рассчитанные `intersection_md/x/y/z` и `result_mode=computed`.
9. Проверить Independent Verifier, result approval и возврат inline `schedule.inc`.
10. Проверить HTTP, Form и Execute Workflow входы.
11. Проверить stale version, неверный gate, неизвестный specialist route и отсутствие секретов/raw payloads в trace.

Последний полный repository smoke официального `n8nio/n8n:2.30.8`: **121 runtime-сценарий**, **29/29 workflow contracts**, **122/122 Code nodes compiled**, clean import/export **23/23**, активных workflow после импорта — **0**. Excel FastAPI image прошёл **18/18** тестов; Math Service дополнительно проверен через HTTP на реальном `.dev` (`200`) и сценарии без пересечения (`404`). Это не заменяет ручной round-trip в корпоративном UI с реальными credentials и Data Tables.
