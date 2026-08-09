# Petroleum Engineering MAS — n8n 2.30.8

Отправная точка управляемой MAS для нефтегазовых задач: первый milestone — создание и проверка tNavigator/ECLIPSE-compatible `SCHEDULE` в режимах `CREATE` и preserve-by-default `REVISE`. Целевой profile — Technical Manual tNavigator 22.2; grid/3D и полный `.DATA` вне текущего scope. Поставка содержит stateful Orchestrator, Excel specialist, governed SCHEDULE Builder, исполнимый hybrid RAG, catalogue-driven decoder/renderer, двухфазный semantic state replay и переносимый HTTPS-adapter проверки в симуляторе. Утверждённое содержимое exact field/semantic catalogue 22.2, immutable artifact store, реальный лицензированный tNavigator runner/check procedure и production golden corpus ещё обязательны до release.

## Состояние компонентов

| Компонент | Статус | Назначение |
|---|---|---|
| `n8n/workflows/universal-engineering-orchestrator.workflow.json` | готовая основа | Planner, durable Data Table state/CAS, HITL, allowlisted routing, retry/replan, independent Verifier |
| Excel Agent + universal adapter | готово | Excel extraction через отдельный FastAPI tool service |
| `engineering-specialist-template.workflow.json` | готовая заготовка | universal `specialist_packet/result` boundary |
| Excel operating-guide PGVector ingestion | готово, локально для Excel | не является будущим MAS-wide hybrid RAG |
| SCHEDULE intake/planner/baseline/renderer/merge/validator/verifier/release workflows | foundation implemented | typed IR рендерится и replay-ится только через accountable machine-readable catalogue; exact grammar/semantics 22.2 требуют licensed manual/sign-off |
| Governed SCHEDULE RAG | runtime implemented | UI ingestion с approval/metadata gate; PostgreSQL lexical/exact + PGVector semantic + tags + deterministic RRF; fail-closed citations/access |
| Redacted MAS trace ledger | bounded foundation implemented | Data Table writer принимает до 100 redacted stage/tool summaries за ответ, включая decision records; внешний PostgreSQL event store и полный low-level tool-event propagation остаются production hardening |
| Math Service и discovery specialist | deferred | после SCHEDULE slice |
| `tnavigator-schedule-builder.workflow.json` | importable foundation | конкретный specialist для `CREATE`/`REVISE`, typed IR, evidence gaps, catalogue decode/render и contract gates; в REVISE автоматически строит hash-bound `PRE_CHANGE_BOUNDARY` из baseline |
| SCHEDULE IR/parser/renderer/linter/merger и release flow | partial foundation | lossless baseline, typed decoder, targeted inventory query, lifecycle/numeric/interval/wildcard replay, atomic merge, verifier и HITL release реализованы; licensed exact 22.2 catalogue content остаётся gate |
| `tnavigator-schedule-simulator-check-adapter.workflow.json` | portable adapter implemented | `SUBMIT/STATUS/RESULT/CANCEL`, durable async continuation и fail-closed release evidence; сам IT-managed runner, его credential и artifact store в репозиторий не входят |

Полная ревизия, архитектура, источники и Definition of Done: **[Petroleum MAS research and roadmap](docs/architecture/petroleum-mas-research-and-roadmap.md)**.

MVP-flow: пользователь вызывает Orchestrator и прикладывает файлы; Orchestrator определяет scope, при необходимости вызывает Excel Extractor, затем обязательный Hybrid Retrieval и только после cited evidence — Schedule Builder. Builder не вызывает Excel/RAG напрямую: `evidence_gap` возвращается Orchestrator, который сохраняет state и возобновляет задачу. Builder fail closed без RAG citations и утверждённого catalogue grammar 22.2.

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
3. `n8n/workflows/tnavigator-schedule-{intake,baseline-analyzer,baseline-decoder,baseline-query,planner,builder,renderer,merge,validator,verifier,release,simulator-check-adapter}.workflow.json`;
4. `n8n/workflows/tnavigator-schedule-{knowledge-ingestion,hybrid-retrieval}.workflow.json`;
5. `n8n/workflows/mas-trace-event-writer.workflow.json`;
6. `n8n/workflows/universal-engineering-orchestrator.workflow.json`;
7. `n8n/workflows/excel-rag-ingestion.workflow.json` — один Test run для Excel operating context.

Фигурные скобки выше — сокращённая запись перечня файлов, а не имя одного JSON. Foundation workflows импортируются отдельно; внутренние production-входы у них только Execute Sub-workflow Trigger.

Не импортируйте в greenfield runtime:

- `excel-mas-orchestrator.workflow.json` — legacy migration-only;
- `engineering-specialist-template.workflow.json` — только шаблон разработки;
- `ai-components.workflow.json` — справочный canvas;
- `excel-extraction-form-adapter.workflow.json` — только если нужна отдельная Excel-форма вне основного orchestrator.

Все JSON поставляются `active:false`. После импорта через UI:

1. В **Call Excel Extraction Specialist Adapter** выберите импортированный adapter.
2. В **Call SCHEDULE Hybrid Retrieval** выберите импортированный Hybrid Retrieval.
3. В **Call SCHEDULE Builder Specialist** выберите импортированный SCHEDULE Builder.
4. В **Call SCHEDULE Simulator Check Adapter** выберите импортированный simulator adapter.
5. В **Call MAS Trace Event Writer** выберите импортированный Trace Writer.
6. В adapter, в **Call native Excel Extraction Agent**, выберите Excel Agent.
7. Создайте Data Table состояния и отдельную Data Table trace по схемам из [n8n/README.md](n8n/README.md), затем выберите их во всех Data Table nodes.
8. Назначьте Planner/Verifier chat credentials, PostgreSQL/PGVector и embedding credentials.
9. В **Runtime configuration** Excel Agent задайте FastAPI URL/API key и отдельный webhook key.
10. В simulator adapter задайте HTTPS `service_url`, утверждённый `check_profile_id` для tNavigator 22.2 и Header Auth credential; настройте governed storage, возвращающий immutable package ref и SHA-256 manifest hash.
11. В ingestion и Excel Agent укажите одну таблицу PGVector и одну embedding model/dimensions; выполните ingestion один раз.
12. Уберите все `REPLACE_*`/красные warnings, выполните smoke checklist и только затем активируйте workflows.

Workflow IDs и credentials нельзя переносимо зашить в JSON, поэтому шесть UI-привязок обязательны. Полная настройка Data Table, RAG, HITL, simulator adapter и трех входов: [n8n/README.md](n8n/README.md).

Важно: Builder содержит pipeline `intake -> [lossless baseline -> catalogue decode -> PRE_CHANGE_BOUNDARY replay -> planning summary] -> planner -> targeted mutation-safe baseline query -> typed IR -> catalogue render -> merge -> candidate replay/validation`. Модель не рендерит authoritative record text и не определяет семантику replay: утверждённый runtime catalogue задаёт layout и generic rules. `MODIFY/REMOVE` принимаются только для target/hash из targeted query; неполный срез блокируется. Orchestrator статически вызывает Hybrid Retrieval перед Builder, превращает `abstain` в HITL, затем требует `immutable artifact -> tNavigator 22.2 simulator pass -> independent verifier -> accountable approval`. Inline preview не считается release artifact. Это importable foundation, а не разрешение выпускать Schedule без exact catalogue 22.2, реального runner evidence и human approval.

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
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-lossless-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-rag-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-decision-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/universal-decision-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-renderer-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-semantic-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-baseline-decoder-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-baseline-query-runtime-smoke.js
docker run --rm -v "$PWD:/workspace:ro" --entrypoint node n8nio/n8n:2.30.8 /workspace/n8n/tests/schedule-simulator-check-runtime-smoke.js
```

Затем выполняется graph/registry/Code-node audit и **чистый import всех 23 delivery JSON в новую пустую БД официального `n8nio/n8n:2.30.8`**. Текущий репозиторный gate: 120 runtime-сценариев, 18 FastAPI/Python tests, 27 workflow-contract tests и clean import `23/23`. Smokes покрывают lossless baseline/atomic merge, catalogue decode, targeted inventory retrieval, автоматический `PRE_CHANGE_BOUNDARY`, catalogue-driven CREATE/REVISE render, lifecycle/numeric/interval/wildcard replay, hybrid RAG, simulator `SUBMIT/STATUS/RESULT/CANCEL` и deterministic scores/decision trace. В целевом UI отдельно обязательны credentials, Data Tables, шесть workflow bindings, HTTP/Form/Sub-workflow, HITL, Excel upload/clarification, CREATE, REVISE, `evidence_gap` resume и настоящий tNavigator runner round-trip. CLI import не проверяет корпоративную сеть, licensed field/semantic catalogue, artifact store, корпоративные credentials или vendor runtime.

Подробный smoke protocol и acceptance gates находятся в [roadmap, раздел 11](docs/architecture/petroleum-mas-research-and-roadmap.md#11-smoke-gate-после-каждого-workflow-change).

## Безопасность

- Не храните API keys в репозитории, export JSON, examples или execution data; найденный/закоммиченный ключ необходимо отозвать и удалить из истории.
- LLM выбирает только logical capability; Code/Switch nodes разрешают статически привязанный workflow.
- Большие инженерные файлы хранятся как immutable artifact references, а не в prompt/Data Table.
- `SCHEDULE` в целевой системе проходит deterministic parser/stateful linter, preservation/diff reconciliation, независимую проверку и human release gate; full `.DATA` generation пока вне scope.
