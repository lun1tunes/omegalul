# Data Table CSV templates — retired

Эти CSV **не** являются живым стейтом MAS. Кейсы, события, HITL и артефакты лежат в Postgres за webhook `MAS — Control Plane Proxy` (`cases`, `events`, `mas_artifacts`, …).

Форма `Form — MAS Deployment Health Check` всё ещё зондирует таблицы `engineering_orchestrator_tasks_v1` и `mas_trace_events_v1`. Отсутствие таблиц → `PASS_WITH_TODO`, это **не** блокер живого контура. Не создавать их «для работы Activity».

Не задавать `ACTIVITY_HYDRATE_URL`. Workflow `Activity — Hydrate` в `retired/`.

Column contracts (для Health Check / архива) — [`../import-manifest.json`](../import-manifest.json) → `data_tables` (`status: retired_not_imported`).

| File | Table name |
|---|---|
| `engineering_orchestrator_tasks_v1.header.csv` | `engineering_orchestrator_tasks_v1` |
| `mas_trace_events_v1.header.csv` | `mas_trace_events_v1` |

Полевой порядок: [`../../docs.md`](../../docs.md).
