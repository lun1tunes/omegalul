# Data Table CSV templates (n8n 2.30.8)

n8n 2.30.8 Data tables: **Create → Import CSV** или From scratch (предпочтительно). Только UI — см. [`docs.md`](../../docs.md) Step 2.

1. Prefer creating tables from scratch with typed columns.
2. Or import these header-only CSVs, then set `version`, `retry_count`, `max_retries` to **Number** on the task table.

Task table is intentionally lean: lifecycle `status`, policy scalars, and a few named JSON bags. Append-only timeline is `mas_trace_events_v1`, not a `history_json` column.

**Wipe на работе:** в UI очистить строки таблиц (схему можно не дропать), см. [`docs.md`](../../docs.md) Step 0c.

| File | Table name |
|---|---|
| `engineering_orchestrator_tasks_v1.header.csv` | `engineering_orchestrator_tasks_v1` |
| `mas_trace_events_v1.header.csv` | `mas_trace_events_v1` |

Column contracts also live in [`../import-manifest.json`](../import-manifest.json) → `data_tables`.
