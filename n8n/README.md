# n8n 2.30.8 — UI-only Petroleum Engineering MAS

Полная пошаговая установка — в корневом [`README.md`](../README.md). Здесь — контракт поставки и границы SCHEDULE.

Целевая версия строго `2.30.8`. Server filesystem, shell, Global Variables и `$env` в workflow не используются.

## Runtime shape

```text
Form / webhook
→ Universal Engineering Orchestrator (CAS + HITL + Verifier)
→ Calculation Adapter | Excel Adapter | SCHEDULE Hybrid Retrieval
→ SCHEDULE Builder (intake→baseline→plan→render→merge→validate→verify)
→ accountable release gate in Orchestrator
→ bounded schedule.inc
```

SCHEDULE delivery — **три** importable workflow плюс shared Trace Writer:

| JSON | UI name |
|---|---|
| `workflows/tnavigator-schedule-knowledge-ingestion.workflow.json` | `SCHEDULE — Knowledge Ingestion` |
| `workflows/tnavigator-schedule-hybrid-retrieval.workflow.json` | `SCHEDULE — Knowledge Retrieval` |
| `workflows/tnavigator-schedule-builder.workflow.json` | `SCHEDULE — Builder` |
| `workflows/mas-trace-event-writer.workflow.json` | `Writer — MAS Trace` |

Отдельных diagnostic mirrors (`intake`, `baseline-*`, `planner`, `renderer`, `merge`, `validator`, `verifier`, `release`) **нет**. Их алгоритмы живут Code-нодами внутри Builder; release policy — в Orchestrator `Apply action and version guard`. Generator `templates/generate_schedule_workflows.py` emit-ит только эти четыре JSON и не должен воскрешать удалённые файлы.

## Import

Канон: [`import-manifest.json`](import-manifest.json).

- `full_clean_import_set` — все **16** `workflows/*.workflow.json`;
- `runtime_import_order` — минимальный порядок для рабочего MVP (см. [`docs/DEPLOY_N8N_UI.md`](../docs/DEPLOY_N8N_UI.md)).

Пользовательский HITL / deploy:

- `mvp-entry-form.workflow.json` — старт задачи + HTML completion (`form` 2.5 `showText`);
- `mas-human-gate-form.workflow.json` — inspect/resume gate: `status` → auto CAS → `reply|approve|reject` → HTML completion;
- `mas-deployment-health-check.workflow.json` — control-plane probes + `where_to_fix` report;
- `data-tables/*.header.csv` — CSV templates for native Data table Import CSV.

Не активировать:

- `excel-mas-orchestrator.workflow.json` — legacy;
- `engineering-specialist-template.workflow.json` — шаблон;
- `ai-components.workflow.json` — справочный canvas;
- `excel-extraction-form-adapter.workflow.json` — только отдельная Excel-форма;
- `excel-rag-ingestion.workflow.json` — одноразовый Test workflow, не Publish.

Все JSON экспортированы с `active: false`.

## Bindings

**11** обязательных Execute Workflow bindings — в [`import-manifest.json`](import-manifest.json) и пошагово в [`docs/DEPLOY_N8N_UI.md`](../docs/DEPLOY_N8N_UI.md) Step 4 (Orchestrator specialists + Entry + Human Gate ×2 + Health Check ×2). Orchestrator для SCHEDULE вызывает только Retrieval и Builder — не отдельные stage-workflow.

## Smoke

```bash
WORKSPACE_ROOT=/path/to/repo node n8n/tests/*.js
```

Скрипты исполняют Code-источники из Builder (и смежных runtime), а не удалённые standalone JSON.
