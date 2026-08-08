# Petroleum Engineering MAS — n8n 2.30.8

Отправная точка управляемой multi-agent системы для нефтегазовых инженерных задач: hydrodynamic model workflows, будущая генерация ECLIPSE/tNavigator `.DATA` и прогнозных `SCHEDULE`, hybrid RAG и детерминированные расчеты. Текущая поставка уже содержит stateful Universal Orchestrator и готовый Excel Extraction specialist; нефтегазовые builders, MAS-wide RAG и Math Service находятся в утверждаемом roadmap, а не выдаются за готовый runtime.

## Состояние компонентов

| Компонент | Статус | Назначение |
|---|---|---|
| `n8n/workflows/universal-engineering-orchestrator.workflow.json` | готовая основа | Planner, durable Data Table state/CAS, HITL, allowlisted routing, retry/replan, independent Verifier |
| Excel Agent + universal adapter | готово | Excel extraction через отдельный FastAPI tool service |
| `engineering-specialist-template.workflow.json` | готовая заготовка | universal `specialist_packet/result` boundary |
| Excel operating-guide PGVector ingestion | готово, локально для Excel | не является будущим MAS-wide hybrid RAG |
| Petroleum capability registry и agent templates | planned | Phase 0–1 |
| Governed hybrid RAG: semantic + lexical + tags | planned | Phase 2 |
| Math Service и discovery specialist | planned | Phase 3 |
| DATA/SCHEDULE builders, parser/linter и release flow | planned | Phase 4–5 |

Полная ревизия, архитектура, источники и Definition of Done: **[Petroleum MAS research and roadmap](docs/architecture/petroleum-mas-research-and-roadmap.md)**.

## Структура репозитория

| Путь | Что это | Настройка |
|---|---|---|
| `n8n/` | n8n 2.30.8 workflows, contracts, templates и Excel RAG seed | только UI n8n; без Global Variables, `$env`, shell и server files |
| `excel-agent-tools/` | детерминированный FastAPI-сервис Excel sessions/tools/artifacts | `excel-agent-tools/excel-tools.env`; Windows CMD или Docker |
| `context-seeder/` | опциональный legacy/CLI способ засеять Excel context напрямую | не нужен при UI-only доступе; основной путь — ingestion workflow |
| `postgres-init/` | локальная Docker Compose инициализация PGVector | только compose-стенд |
| `docs/architecture/` | целевая MAS-архитектура и roadmap | документация |

Корневой `.env` относится **только** к локальному Docker Compose. Секреты не должны храниться в workflow JSON или документации.

## Быстрый greenfield запуск: Windows CMD + n8n UI

### 1. Excel FastAPI без Docker

Требуется Python 3.11–3.13:

```bat
cd excel-agent-tools
setup-windows.bat
copy excel-tools.env.example excel-tools.env
notepad excel-tools.env
start-windows.bat
```

Во втором CMD:

```bat
cd excel-agent-tools
check-windows.bat
```

Если n8n работает на сервере, `127.0.0.1:8000` указывает на сервер n8n, а не на ваш ПК. В `excel_tools_url` задайте разрешенный IT адрес Windows-машины, включите `EXCEL_TOOLS_HOST=0.0.0.0` и откройте порт только для n8n. Детали: [excel-agent-tools/README.md](excel-agent-tools/README.md).

### 2. Импорт в UI n8n 2.30.8

Импортировать runtime-файлы в порядке:

1. `n8n/workflows/excel-extraction-agent.workflow.json`;
2. `n8n/workflows/excel-engineering-specialist-adapter.workflow.json`;
3. `n8n/workflows/universal-engineering-orchestrator.workflow.json`;
4. `n8n/workflows/excel-rag-ingestion.workflow.json` — один Test run для Excel operating context.

Не импортируйте в greenfield runtime:

- `excel-mas-orchestrator.workflow.json` — legacy migration-only;
- `engineering-specialist-template.workflow.json` — только шаблон разработки;
- `ai-components.workflow.json` — справочный canvas;
- `excel-extraction-form-adapter.workflow.json` — только если нужна отдельная Excel-форма вне основного orchestrator.

Все JSON поставляются `active:false`. После импорта через UI:

1. В **Call Excel Extraction Specialist Adapter** выберите импортированный adapter.
2. В adapter, в **Call native Excel Extraction Agent**, выберите Excel Agent.
3. Создайте Data Table по схеме из [n8n/README.md](n8n/README.md) и выберите ее во всех Data Table nodes.
4. Назначьте Planner/Verifier chat credentials, PostgreSQL memory/PGVector и embedding credentials.
5. В **Runtime configuration** Excel Agent задайте FastAPI URL/API key и отдельный webhook key.
6. В ingestion и Excel Agent укажите одну таблицу PGVector и одну embedding model/dimensions; выполните ingestion один раз.
7. Уберите все `REPLACE_*`/красные warnings, выполните smoke checklist и только затем активируйте workflows.

Workflow IDs и credentials нельзя переносимо зашить в JSON, поэтому две UI-привязки обязательны. Полная настройка Data Table, HITL и трех входов: [n8n/README.md](n8n/README.md).

## Три входа orchestrator

- authenticated HTTP Webhook;
- n8n Form Trigger;
- Execute Sub-workflow из другого workflow.

HITL не держит Wait execution: состояние и gate сохраняются, а `reply/approve/reject/retry/cancel/status` приходят новым вызовом с `task_id`, актуальной `expected_version` и `gate_id`. Approval требует внешне аутентифицированного accountable actor.

## Локальный Docker Compose

```bash
cp .env.example .env
# заполнить обязательные секреты
docker compose up --build -d
```

Поднимаются pinned n8n 2.30.8, runners, PostgreSQL/PGVector и Excel tools. Context-seeder в runtime не входит.

## Smoke gate

После каждого изменения workflow обязательно:

```bash
cd excel-agent-tools
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
cd ..
python3 - <<'PY'
import glob, json
for path in glob.glob('n8n/workflows/*.workflow.json'):
    json.load(open(path, encoding='utf-8'))
print('workflow JSON: OK')
PY
```

Затем выполняется автоматический graph/registry/Code-node audit и **чистый import всех delivery JSON в новую пустую БД официального `n8nio/n8n:2.30.8`**. В целевом UI отдельно проверяются credentials, Data Table, workflow bindings, HTTP/Form/Sub-workflow, HITL, Excel upload/clarification и RAG. CLI import не проверяет корпоративную сеть и секреты.

Подробный smoke protocol и acceptance gates находятся в [roadmap, раздел 10](docs/architecture/petroleum-mas-research-and-roadmap.md#10-smoke-gate-после-каждого-изменения-workflow).

## Безопасность

- Не храните API keys в репозитории, export JSON, examples или execution data; найденный/закоммиченный ключ необходимо отозвать и удалить из истории.
- LLM выбирает только logical capability; Code/Switch nodes разрешают статически привязанный workflow.
- Большие инженерные файлы хранятся как immutable artifact references, а не в prompt/Data Table.
- `.DATA`/`SCHEDULE` в целевой системе всегда проходят deterministic parser/linter, независимую проверку и human release gate.
