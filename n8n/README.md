# n8n 2.30.8: импорт только через UI

Workflows рассчитаны строго на n8n 2.30.8 и не требуют Global Variables, `$env`, shell или доступа к серверным файлам.

Текущая runtime-поставка — базовый Universal Engineering Orchestrator и готовый Excel specialist. Petroleum capability registry, MAS-wide hybrid RAG, Math Service, `.DATA`/`SCHEDULE` builders и deterministic deck validator пока находятся в плане и не должны восприниматься как уже реализованные workflow. Целевая архитектура, источники и поэтапный Definition of Done описаны в [`docs/architecture/petroleum-mas-research-and-roadmap.md`](../docs/architecture/petroleum-mas-research-and-roadmap.md).

## Одна основная архитектура

`Universal Engineering Orchestrator` — единственная точка оркестрации инженерных задач. Excel Extraction уже предусмотрен в его allowlisted routing как готовый specialist. Физически specialist вынесен в два sub-workflow: универсальный adapter и прикладной Excel Agent. Такое разделение сохраняет небольшой control-plane, независимую проверку и строгую границу контрактов; это не второй параллельный orchestrator.

Старый `excel-mas-orchestrator.workflow.json` оставлен только для миграции и в новую установку не импортируется.

## Universal Engineering Orchestrator

### Что импортировать

Для рабочей поставки с Excel импортируйте через UI в таком порядке:

1. `workflows/excel-extraction-agent.workflow.json` — прикладной Excel specialist с FastAPI tools.
2. `workflows/excel-engineering-specialist-adapter.workflow.json` — переводит универсальный контракт в нативный Excel-вызов и обратно.
3. `workflows/universal-engineering-orchestrator.workflow.json` — единственный stateful control-plane: планирование, allowlisted delegation, независимая проверка, retry/replan и Human-in-the-Loop.
4. `workflows/excel-rag-ingestion.workflow.json` — одноразовое наполнение Excel operating context, если specialist использует RAG.

`workflows/engineering-specialist-template.workflow.json` импортируйте только при разработке новых специалистов. Это заготовка, а не runtime-зависимость основной схемы. `workflows/excel-extraction-form-adapter.workflow.json` нужен только для отдельной standalone-формы Excel Agent; основной orchestrator уже имеет собственную форму.

После импорта обязательно сделайте две привязки через UI:

1. В Universal Orchestrator, в ноде **Call Excel Extraction Specialist Adapter**, выберите импортированный Excel adapter.
2. В Excel adapter, в ноде **Call native Excel Extraction Agent**, выберите импортированный Excel Agent.

Workflow ID не переносимы между инсталляциями n8n, поэтому эти две привязки намеренно не зашиты в JSON. Все workflow поставляются с `active: false`.

Переносимые шаблоны инструкций и контрактов:

- `templates/engineering-task-instruction.template.json` — рекомендуемая структура постановки инженерной задачи;
- `templates/orchestrator-instruction.template.md` — политика Planner;
- `templates/specialist-workflow-instruction.template.md` — политика specialist workflow;
- `templates/specialist-result-contract.schema.json` — универсальный `specialist_result` v1.0.

### Durable state без внешнего сервиса

Создайте через UI n8n одну **Data Table** с именем, например `engineering_orchestrator_tasks_v1`. Колонку `id` создавать нельзя: у Data Table она встроенная.

| Колонка | Тип Data Table |
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

После импорта откройте **каждый фиолетовый Data Table node** и выберите эту таблицу из списка. Состояние задачи хранится только здесь; Chat Memory не является authoritative state и в шаблоне не используется.

Для `task_id` должен действовать уникальный организационный инвариант: одна задача — одна строка. Если Data Table UI вашей сборки позволяет задать unique constraint/index, задайте его для `task_id`. В любом случае workflow запрашивает максимум две строки и fail closed при нуле или дубликате.

Все изменения выполняются как optimistic compare-and-set: Update фильтруется одновременно по `task_id` и предыдущей `version`. После каждого Update отдельный Code node подтверждает, что обновлена именно ожидаемая строка; нулевой match превращается в `conflict`, поэтому устаревший запрос не продолжает планирование или делегирование. Клиент перечитывает `status` и повторяет действие с новой версией. Для нескольких n8n workers используйте одну и ту же Data Table и не обходите этот фильтр прямыми записями.

При загрузке задачи workflow проверяет версию, статус, класс риска, retry-счётчики и тип каждого durable JSON-поля. Повреждённая или вручную некорректно изменённая строка возвращает `conflict` с фазой `state_integrity`; Planner, делегирование и запись поверх такой строки не запускаются. Восстановление выполняется вручную ответственным администратором по журналу/резервной копии.

Workflow намеренно не обещает дедупликацию команды `start`: нативная Data Table не даёт этому шаблону переносимого атомарного unique/upsert-контракта через UI. Сетевой повтор `start` может создать новую задачу. После первого успешного ответа клиент обязан сохранить `task_id` и для продолжения использовать только `status`, `reply`, `approve`, `reject`, `retry` или `cancel` с актуальной `version`.

Компактные request/context/result ограничены по размеру в Code nodes. Большие расчётные модели, файлы и массивы данных передавайте только как immutable/governed `artifact_refs`.

Structured `request`/`request_json` и `context`/`context_json` валидируются до создания задачи. Некорректный JSON, массив вместо объекта или payload сверх лимита возвращают validation conflict и не попадают в Planner: structured input не превращается молча в пустой `{}`. Если structured request не передан, `request_text` остаётся допустимым кратким fallback для intake.

### Настройка Planner и Verifier

Через UI назначьте chat-model credentials в двух нодах:

- `Planner Chat Model — configure in UI`;
- `Verifier Chat Model — separate credential`.

Verifier логически и, желательно, credential/model deployment должен быть отделён от Planner и specialist. Модель не управляет состояниями и не вызывает произвольные workflow: она возвращает только структурированный план или verdict, а Code/IF/Switch-ноды применяют детерминированную политику.

### Подключение specialist workflows

1. Клонируйте `engineering-specialist-template.workflow.json`.
2. Измените его системную инструкцию под **одну ограниченную способность** и добавьте нужные штатные n8n tool nodes.
3. Не меняйте внешний boundary: вход `specialist_packet` v1.0, выход `specialist_result` v1.0.
4. В orchestrator выберите импортированный workflow в соответствующей ноде:
   - `Call Calculation Specialist`;
   - `Call Data Specialist`;
   - `Call Document Specialist`.
5. В Code node `Resolve allowlisted specialist` поставьте `configured: true` только для реально настроенной ветки.
6. В Planner catalogue добавляйте только логические `specialist_id` и capabilities. Workflow ID никогда не помещается в prompt и не выбирается моделью.

Excel Extractor уже добавлен в allowlist как `excel_extraction_specialist`. Universal Orchestrator вызывает только тонкий adapter workflow: adapter получает `specialist_packet`, переводит его в нативный вход Extractor и возвращает универсальный `specialist_result`. Нативные идентификаторы продолжения остаются внутри непрозрачного `continuation`; control-plane не знает URL и FastAPI-инструментов и не доверяет модели выбирать эти идентификаторы.

При первом Excel-вызове binary-поле `file` передаётся в sub-workflow напрямую средствами Execute Workflow и не записывается в Data Table или prompt. Если до первого делегирования открылся approval gate, файл нужно приложить снова при `approve`. После нативного Excel-уточнения повторная загрузка файла не нужна: adapter использует сохранённое непрозрачное продолжение и свежий ответ человека.

### Human-in-the-Loop без зависших execution

Шаблон намеренно не использует Wait node. При необходимости решения он сохраняет `awaiting_human` и возвращает:

- `task_id`;
- `version` / `expected_version`;
- `human_gate.gate_id`;
- `human_gate.kind` и вопросы.

Ответ человека — новый запуск через Form, authenticated Webhook или Execute Workflow Trigger:

```json
{
  "action": "reply",
  "task_id": "eng_...",
  "expected_version": 3,
  "gate_id": "gate_eng_...",
  "human_response": {
    "answers": [
      {"question_id": "units", "answer": "SI: N, mm, MPa"}
    ]
  },
  "requested_by": "lead_engineer"
}
```

Для approval используйте `action: approve`; отклонение — `reject`; чтение — `status`; управляемый повтор — `retry`; отмена — `cancel`. `retry` принимается только для durable-состояния `retryable_error`; когда лимит повторов исчерпан или открыт `needs_decision`/approval gate, человек должен продолжить через соответствующий `reply`/`approve`/`reject` с актуальными `expected_version` и `gate_id`. Устаревшая `expected_version` или неверный `gate_id` fail closed.

Для `approve`/`reject` обязательно передавайте accountable `requested_by`; анонимное решение отклоняется. При этом `requested_by` — только audit metadata, а не доказательство личности: поле JSON может подменить вызывающий. Право принимать решения должно проверяться до входа в workflow — через n8n user auth/SSO для формы либо Header Auth/API gateway с отдельной политикой доступа для webhook и вызывающих workflow. Не выдавайте approval-доступ обычным инициаторам задач. Журнал состояния и список human responses ограничены по длине, чтобы одна долгоживущая задача не раздувала Data Table без границ.

### Risk и verification gates

- `critical`: human approval до delegation и повторно после независимой проверки;
- `high`: human approval после независимой проверки;
- `low`: после успешной независимой проверки может завершаться автоматически.

Риск вычисляется fail-closed: новый planner risk не может понизить уже сохранённый `risk_class` или явно объявленный риск в request. Начальное `low` в новой строке — нейтральное значение до первого плана; неизвестный/пропущенный риск Planner повышается до `high`.

Проверяются единицы/размерности, provenance и revisions, governing standards, coordinate systems, load cases, boundary conditions, tolerances, assumptions, uncertainty/margins, acceptance criteria, evidence и воспроизводимость. Specialist self-check обязателен, но не считается независимой проверкой.

### Минимальный приёмочный тест до activation

1. `start` без objective → `awaiting_human` и `needs_input`.
2. Полный low-risk запрос → delegation → verifier → `completed`.
3. Critical запрос → pre-delegation approval; неверный `gate_id` отклоняется.
4. High/critical успешный результат → `result_approval`, не автоматический `completed`.
5. Specialist `needs_input`, `needs_decision`, `needs_approval`, `retryable_error`, `fatal_error` проходят по разным веткам.
6. Verifier `retry` вызывает bounded replan; после `max_retries` открывается HITL gate.
7. Два ответа с одинаковой старой version: только актуальный переход допустим, второй получает conflict/status reload.
8. Model output с неизвестным `specialist_id` не вызывает никакой workflow.
9. В state остаются только compact JSON и artifact references, не большие расчётные файлы.

Не активируйте Webhook до выбора Header Auth credential, Data Table и всех обязательных credentials/workflow bindings.

## Excel specialist: состав поставки

- `workflows/excel-extraction-agent.workflow.json` — прикладной specialist с HTTP webhook, AI Agent, Postgres memory и семью FastAPI-инструментами.
- `workflows/excel-engineering-specialist-adapter.workflow.json` — обязательная граница между универсальным и нативным Excel-контрактами.
- `workflows/excel-rag-ingestion.workflow.json` — ручная одноразовая загрузка operating guide в PGVector.
- `workflows/excel-extraction-form-adapter.workflow.json` — необязательная standalone-форма для прямого запуска Excel Agent вне основного orchestrator.
- `workflows/excel-mas-orchestrator.workflow.json` — legacy migration-only; в новую установку не импортировать.

`ai-components.workflow.json` — справочный шаблон Qwen/OpenAI-compatible компонентов, не часть основного runtime.

Все delivery JSON намеренно имеют `active: false`. После импорта оставьте их выключенными до завершения настройки: webhook/form не должны принимать запросы с `REPLACE_*` и непривязанными credentials.

Excel Agent очищен до 56 узлов без потери функций. Удалены отключённые дубликаты tools, осиротевшие узлы и старый строковый транспорт HTTP Tool v1. Семь агентских HTTP Request Tool используют структурированные параметры версии 1.1. Проверка, экспорт, разрешение уточнения и финализация выполняются детерминированной веткой обычных HTTP-узлов, поэтому дублировать их как AI tools не требуется. Сам HTTP Request Tool не является deprecated.

Нативный Excel workflow не раздроблен на множество технических sub-workflow. Отдельно вынесен только adapter как anti-corruption boundary между универсальным `specialist_packet/result` и прикладным Excel-протоколом. Для основной поставки через UI нужно сделать ровно две Execute Workflow-привязки, перечисленные в начале документа.

## Совместимость нод n8n 2.30.8

JSON использует штатные **registry identifiers** из официального n8n 2.30.8:

- AI Agent — `@n8n/n8n-nodes-langchain.agent`, версия 3.1;
- HTTP Request Tool — `@n8n/n8n-nodes-langchain.toolHttpRequest`, версия 1.1;
- Form Trigger — `n8n-nodes-base.formTrigger`, версия 2.6.

В UI Agent отображается как **AI Agent**, а в справочной ссылке/интерфейсе может встречаться текст `n8n-nodes-langchain.agent`. Это не означает, что такой текст надо записывать в поле `type`: официальный экспорт 2.30.8 содержит scoped registry ID `@n8n/n8n-nodes-langchain.agent`. Автоматическая замена на `n8n-nodes-langchain.agent` сделает JSON несовместимым со штатным пакетом `@n8n/n8n-nodes-langchain`.

Чистый CLI-импорт всех delivery-файлов проверяется на официальном образе `n8nio/n8n:2.30.8`, а типы и `typeVersion` универсального комплекта дополнительно закреплены контрактным тестом. Однако корпоративный администратор может отключить AI/community package или использовать модифицированную сборку под тем же номером версии. Если после импорта конкретная нода красная как неизвестная, пришлите экспорт минимального рабочего workflow с одной такой нодой из **этого рабочего UI**; только он является точным источником registry ID вашей сборки.

`n8n-nodes-base.form` (в UI **n8n Form**) — это страница шага/результата формы, а не триггер. Стартовая нода называется **n8n Form Trigger** и экспортируется как `n8n-nodes-base.formTrigger` версии 2.6. В штатном 2.30.8 присутствуют обе ноды. Поэтому универсальный orchestrator начинает Form-ветку через `formTrigger`, а Excel form adapter использует `formTrigger` на входе и `form` только для показа результата. Слепая замена trigger на `form` сделает workflow неисполняемым.

## Настройка Excel Agent

После импорта откройте в Excel Agent узел **Runtime configuration**:

| Поле | Значение |
|---|---|
| `excel_tools_url` | доступный с сервера n8n адрес, включая `/api/v1` |
| `excel_tools_api_key` | тот же `API_KEY`, что в `excel-tools.env` |
| `excel_webhook_api_key` | отдельный длинный секрет для `X-Excel-Webhook-Key` |

Затем через UI назначьте credentials в узлах:

- Chat Model — дешёвая nano-модель, temperature 0;
- Postgres Chat Memory — рабочая PostgreSQL БД;
- OpenAI Embeddings — `text-embedding-3-small` либо корпоративный совместимый embedding;
- PGVector operating context — та же PostgreSQL БД и таблица, которую наполнит RAG workflow.

Секреты вводятся в credentials/узел Runtime configuration. Ничего не нужно добавлять в Global Variables.

Импортированный JSON содержит только имена/заглушки credentials: сами секреты n8n не экспортирует. Откройте каждый узел с красным предупреждением и выберите существующий credential из списка либо создайте его через **Create new credential**. Поля подключения PostgreSQL (`host`, `port`, `database`, `user`, `password`, SSL) выдаёт IT; для OpenAI-compatible модели дополнительно задаются API key и Base URL в соответствующем credential.

## Первичное наполнение RAG

Импортируйте `excel-rag-ingestion.workflow.json`, но не активируйте его.

1. В **RAG runtime configuration** задайте новое имя таблицы, например `n8n_excel_agent_context_v1`.
2. В embedding-узле выберите корпоративную embedding-модель.
3. В PGVector-узле выберите PostgreSQL credential.
4. Нажмите **Test workflow** один раз и убедитесь, что загружены все документы.
5. В Excel Agent задайте ту же таблицу и тот же embedding credential.

Размерность при записи и чтении обязана совпадать. При смене embedding-модели используйте новую таблицу. Повторный Test workflow может создать дубликаты, поэтому для чистой переиндексации также используйте новую таблицу или согласованную с IT очистку.

Канонический переносимый источник контекста: `rag/excel-agent-operating-guide.documents.json`. Его содержимое уже встроено в ingestion workflow, поэтому доступ к серверной файловой системе не нужен.

После успешной загрузки откройте Excel Agent и вручную проверьте оба узла: **PGVector operating context** должен иметь ровно то же имя таблицы, а его дочерний embedding-узел — ту же модель/размерность, что и ingestion workflow.

## Три входа

- **HTTP:** authenticated webhook Universal Orchestrator; нативный Excel webhook оставлен для прямой диагностики specialist.
- **Form:** встроенный Form Trigger Universal Orchestrator принимает инженерную задачу и необязательный Excel-файл. Standalone Excel form adapter нужен только для прямого запуска specialist вне orchestrator.
- **Другой workflow:** вызывайте Universal Orchestrator через Execute Workflow и передавайте JSON плюс binary-поле `file` для нового Excel-запуска.

ID workflow из другой инсталляции переносить нельзя. После импорта выберите adapter в **Call Excel Extraction Specialist Adapter**, затем Excel Agent в **Call native Excel Extraction Agent**. Если используется standalone-форма, отдельно выберите Excel Agent в **Call Excel Extractor core**. Legacy MAS для новой архитектуры не нужен.

Уточнение продолжается с тем же `meta.session_id`, точным `clarification.token` и ответами. Excel повторно не загружается. Workflow сам разрешает уточнение и загружает `continuation_state` до запуска агента.

## Перед активацией

- все узлы без красных credential warnings;
- в двух Execute Workflow-нодах выбраны реальные adapter и Excel Agent, а не `REPLACE_*`;
- во всех фиолетовых Data Table-нодах Universal Orchestrator выбрана одна созданная таблица;
- Planner и Verifier Universal Orchestrator имеют настроенные chat credentials;
- Runtime configuration Excel Agent не содержит `REPLACE_*`;
- n8n-сервер видит `/health` FastAPI по настроенному адресу;
- RAG ingestion завершился успешно;
- Excel Agent указывает на ту же RAG-таблицу и embedding;
- webhook Universal Orchestrator защищён credential и проверен с корректным и некорректным ключом;
- через Universal Orchestrator проверены новый Excel upload, уточнение/продолжение и повтор устаревшего ответа.
