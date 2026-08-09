# Petroleum Engineering MAS — n8n 2.30.8

Практичный внутренний MVP для создания и проверки секции `SCHEDULE` в режимах `CREATE` и preserve-by-default `REVISE`. Система работает с обычным текстом `.data/.inc` внутри n8n и возвращает готовый текст `schedule.inc`.

Актуальный путь выполнения:

```text
задача + optional .data/.inc + optional Excel
→ Universal Engineering Orchestrator
→ optional Calculation Adapter → Math Service (.dev + CPS3 → JSON)
→ hybrid RAG по экспертным инструкциям
→ SCHEDULE Builder
→ deterministic validation
→ Independent Verifier
→ HITL при необходимости
→ inline schedule.inc
```

В MVP нет внешнего хранилища SCHEDULE-файлов, автоматического запуска tNavigator и отдельного workflow оценки RAG. Эти функции не являются зависимостями текущей схемы.

## Состав репозитория

| Путь | Назначение |
|---|---|
| `n8n/` | workflow JSON, шаблоны и smoke-тесты для n8n строго `2.30.8` |
| `excel-agent-tools/` | FastAPI-сервис Excel tools; запускается на Windows без Docker или в контейнере |
| `fastapi-math-service/` | простой FastAPI/NumPy math-сервис; первый endpoint вычисляет пересечение `.dev`-траектории и ASCII CPS3-поверхности |
| `context-seeder/` | опциональная CLI-загрузка Excel operating context; для SCHEDULE RAG не требуется |
| `postgres-init/` | инициализация локального PostgreSQL/PGVector для Docker Compose |
| `docs/architecture/` | архитектура и roadmap нефтегазовой MAS |

Переменные разделены:

- корневой `.env` — только локальный `docker-compose.yml`;
- `excel-agent-tools/excel-tools.env` — только FastAPI Excel Tools;
- Math Service не требует env, API key или credentials в текущем локальном MVP;
- credentials PostgreSQL, OpenAI-compatible моделей и embeddings задаются в UI n8n;
- Global Variables и `$env` в workflow не используются.

## Что импортировать в n8n

Используйте n8n строго `2.30.8`. Канонический перечень и порядок находятся в [`n8n/import-manifest.json`](n8n/import-manifest.json).

Минимальный runtime-набор:

1. `calculation-specialist-adapter.workflow.json`;
2. `excel-extraction-agent.workflow.json`;
3. `excel-engineering-specialist-adapter.workflow.json`;
4. `tnavigator-schedule-knowledge-ingestion.workflow.json`;
5. `tnavigator-schedule-hybrid-retrieval.workflow.json`;
6. `tnavigator-schedule-builder.workflow.json`;
7. `mas-trace-event-writer.workflow.json`;
8. `universal-engineering-orchestrator.workflow.json`.

Builder содержит необходимые deterministic SCHEDULE stages внутри своей схемы. Отдельные `tnavigator-schedule-*` foundation workflows из manifest можно импортировать для диагностики и развития, но основной Orchestrator вызывает только Retrieval и Builder.

После импорта в UI выполните шесть обязательных Execute Workflow bindings:

1. Orchestrator → Excel Adapter;
2. Orchestrator → SCHEDULE Hybrid Retrieval;
3. Orchestrator → SCHEDULE Builder;
4. Orchestrator → Calculation Adapter;
5. Orchestrator → MAS Trace Writer;
6. Excel Adapter → Excel Extraction Agent.

Затем выберите Data Tables и credentials. Все workflow поставляются `active:false`; не активируйте входные workflow, пока не устранены `REPLACE_*`.

Подробная настройка: [`n8n/README.md`](n8n/README.md).

## SCHEDULE Knowledge Ingestion

`tnavigator-schedule-knowledge-ingestion` принимает через Form или Execute Workflow подготовленный экспертом блок:

- `keyword_instruction` — полная инструкция по keyword;
- `worked_example` — рабочий пример задачи и фрагмента SCHEDULE;
- keywords, topics и task patterns для точного/tag-поиска;
- optional `schema_catalogue_json` с точным порядком полей и правилами deterministic render.

Данные пишутся в PostgreSQL/PGVector. Retrieval объединяет lexical PostgreSQL search, semantic PGVector search, exact tags и deterministic RRF, затем возвращает полный активный parent document. Для ingestion и retrieval должна использоваться одна embedding-модель и одинаковая размерность.

## Excel FastAPI на Windows без Docker

Нужен Python 3.11–3.13 и обычный CMD:

```bat
cd excel-agent-tools
setup-windows.bat
copy excel-tools.env.example excel-tools.env
notepad excel-tools.env
start-windows.bat
```

Проверка во втором CMD:

```bat
cd excel-agent-tools
check-windows.bat
```

Если n8n работает не на том же ПК, адрес `127.0.0.1:8000` ему недоступен. Укажите в Excel Agent сетевой адрес Windows-машины, задайте `EXCEL_TOOLS_HOST=0.0.0.0` и ограничьте firewall доступом только со стороны n8n.

Детали: [`excel-agent-tools/README.md`](excel-agent-tools/README.md).

## Math FastAPI на Windows без Docker

```bat
cd fastapi-math-service
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

В `calculation-specialist-adapter` отредактируйте видимое поле
`Math Service Configuration`, если n8n находится не на этом ПК. Auth и env у
сервиса намеренно отсутствуют. Адаптер отправляет все приложенные `.dev` одним
batch-запросом и одну поверхность; ответ сохраняет исходные имена файлов.
Траектории и поверхность должны использовать
одинаковые CRS, единицы, datum и направление оси Z. Детали:
[`fastapi-math-service/README.md`](fastapi-math-service/README.md).

## Локальный Docker Compose

```bash
cp .env.example .env
docker compose up --build -d
```

Compose поднимает pinned n8n `2.30.8`, PostgreSQL/PGVector и Excel Tools. Docker не обязателен для запуска Excel FastAPI на рабочей Windows-машине.

## Проверка перед активацией

- импортировать JSON через UI n8n `2.30.8`;
- настроить шесть bindings, две Data Tables, credentials и URL Math Service;
- загрузить хотя бы одну `keyword_instruction` и проверить Hybrid Retrieval;
- проверить `CREATE` без baseline;
- проверить `REVISE` с приложенным `.data/.inc` и сохранением незатронутых блоков;
- проверить Excel `evidence_gap` → уточнение → resume Builder;
- проверить неверный `gate_id` и stale `expected_version`;
- убедиться, что итог содержит `release.filename = schedule.inc` и `release.schedule_text`.

Последний полный repository smoke в официальном image `n8nio/n8n:2.30.8`: **121 runtime-сценарий**, **18/18 Excel FastAPI tests**, **29/29 workflow contracts**, **122/122 Code nodes compiled**, чистый import/export **23/23**, активных после импорта — **0**. Math Service дополнительно проверен через HTTP на реальном `.dev`: пересечение `200`, отсутствие пересечения `404`. Целевой UI всё равно требует ручной проверки credentials, Data Tables и network round-trip.

## Безопасность MVP

- секреты не хранятся в JSON, README или Data Table;
- LLM выбирает logical capability, а Code/Switch разрешают только статически allowlisted workflow;
- полный Excel binary и лишние копии `.data/.inc` не сохраняются в durable state;
- скрытый chain-of-thought не публикуется: trace содержит decision records, reason codes, tool IDs/hashes и результаты проверок;
- результат SCHEDULE проходит deterministic validation, Independent Verifier и применимый HITL gate.
