Ниже — практичная, минимально бюрократичная схема под ваш стек:

- n8n как оркестратор;
- Postgres как state + events + error traces;
- FastAPI сервисы агентов;
- Qwen/OpenAI-нода для выбора агента;
- UI видит realtime-ленту событий, handoff, HITL и ошибки.

Главный принцип:

> Оркестратор не хардкодит флоу. Он смотрит state, цель, доступные данные и сам решает, какому агенту передать задачу.  
> Каждая передача задачи — событие в UI.  
> Каждая ошибка ноды n8n — отдельный error trace, не смешанный с бизнес-ошибками агентов.

---

# 1. Общая схема

```text
                        ┌─────────────┐
                        │     UI      │
                        │  timeline   │
                        └──────┬──────┘
                               │ events / state / errors
                               ▼
┌────────────────────────────────────────────────────────┐
│                     Postgres                           │
│                                                        │
│  cases        state задачи                             │
│  events       realtime лента: handoff, status, HITL    │
│  error_traces nativ n8n node errors                    │
│  executions   execution_id -> case_id                  │
└───────────────┬────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────┐
│                   n8n Orchestrator                     │
│                                                        │
│  1. Trigger                                             │
│  2. Load / init state                                   │
│  3. Decision LLM: выбрать агента                        │
│  4. Dispatch agent                                      │
│  5. Merge result / update state                         │
│  6. Emit events                                         │
│  7. Loop / HITL / finish                                │
└───────┬───────────────┬───────────────┬────────────────┘
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ excel        │ │ schedule     │ │ calculation  │
│ extractor    │ │ builder      │ │ agent        │
│ agent        │ │ agent        │ │              │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ excel-service│ │ schedule-    │ │ calc-service │
│ FastAPI      │ │ builder-     │ │ FastAPI      │
│              │ │ service      │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
```

---

# 2. Минимальные таблицы в Postgres

## 2.1. `cases`

```sql
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY,
    state JSONB NOT NULL,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

`status`:

```text
new
running
waiting_user
done
failed
```

---

## 2.2. `events`

Это главная лента для UI.

```sql
CREATE TABLE events (
    event_id BIGSERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    task_id TEXT,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    agent_id TEXT,
    status TEXT,
    status_message TEXT,
    handoff_message TEXT,
    payload JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Поля, которые вы просили:

```text
status_message   → текущий статус внутри оркестратора
handoff_message  → сообщение агенту в стиле «специалист специалисту»
```

Пример:

```json
{
  "status_message": "Сбор данных по датам ввода из Excel",
  "handoff_message": "Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты. Если формат дат неоднозначный — верни needs_input."
}
```

---

## 2.3. `error_traces`

Это именно **node-level ошибки n8n**, не бизнес-ошибки агентов.

```sql
CREATE TABLE error_traces (
    error_id BIGSERIAL PRIMARY KEY,
    case_id TEXT,
    execution_id TEXT,
    workflow_name TEXT,
    node_name TEXT,
    error_message TEXT,
    error_type TEXT,
    stack TEXT,
    input_snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## 2.4. `executions`

Нужен, чтобы Error Workflow знал, к какому `case_id` относится упавший execution.

```sql
CREATE TABLE executions (
    execution_id TEXT PRIMARY KEY,
    case_id TEXT,
    workflow_name TEXT,
    started_at TIMESTAMPTZ DEFAULT now()
);
```

В начале основного workflow:

```sql
INSERT INTO executions(execution_id, case_id, workflow_name)
VALUES ($execution.id, $json.case_id, 'orchestrator');
```

Тогда Error Workflow сможет найти `case_id` по `execution_id`.

---

# 3. Типы событий

Минимальный набор:

```text
case.created
case.finished
case.failed

orchestrator.status
orchestrator.decision

agent.handoff
agent.accepted
agent.progress
agent.result
agent.failed

hitl.request
hitl.answered

system.node_error
```

---

# 4. Ключевой realtime event: handoff

Каждый раз, когда оркестратор решает передать задачу агенту, он пишет:

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-001",
  "kind": "agent.handoff",
  "actor": "orchestrator",
  "agent_id": "excel_extractor",
  "status": "delegated",
  "status_message": "Сбор данных по датам ввода из Excel",
  "handoff_message": "Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты. Если колонки неоднозначные — верни needs_input.",
  "payload": {
    "inputs": {
      "excel_artifact": "files/wells_2026.xlsx"
    }
  }
}
```

Агент, когда принял задачу, пишет:

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-001",
  "kind": "agent.accepted",
  "actor": "excel_extractor",
  "agent_id": "excel_extractor",
  "status": "accepted",
  "status_message": "Разбираю Excel",
  "handoff_message": null,
  "payload": {}
}
```

После завершения:

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-001",
  "kind": "agent.result",
  "actor": "excel_extractor",
  "agent_id": "excel_extractor",
  "status": "completed",
  "status_message": "Извлечены даты ввода по 3 скважинам",
  "handoff_message": null,
  "payload": {
    "table_id": "TBL-001",
    "rows": 42
  }
}
```

UI из этого рисует timeline:

```text
CASE-001

[orchestrator]
Принял задачу: обновить SCHEDULE по новым датам из Excel

[orchestrator → excel_extractor]
Сбор данных по датам ввода из Excel
Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты.

[excel_extractor]
Разбираю Excel

[excel_extractor]
Извлечены даты ввода по 3 скважинам

[orchestrator → schedule_builder]
Формирование изменений SCHEDULE
Schedule Builder, вот данные из Excel. Собери изменения DATES и WCONPROD.
```

---

# 5. Минимальный JSON state

State должен быть маленьким. Вся история — в `events`.

```json
{
  "case_id": "CASE-001",
  "goal": "Обновить SCHEDULE по новым датам запуска из Excel",
  "status": "running",

  "plan": [
    {
      "id": "extract_excel",
      "title": "Извлечь данные из Excel",
      "status": "done"
    },
    {
      "id": "calc_perf_top",
      "title": "Найти начало интервала перфорации",
      "status": "pending"
    },
    {
      "id": "build_schedule",
      "title": "Собрать изменения SCHEDULE",
      "status": "pending"
    }
  ],

  "artifacts": {
    "excel": "files/wells_2026.xlsx",
    "schedule_source": "models/base_schedule.inc",
    "surface": "geo/struct_surface.xyz",
    "trajectory": "well/trajectory_101.csv",
    "schedule_out": null,
    "diff": null
  },

  "data": {
    "excel": null,
    "calc": null,
    "schedule": null
  },

  "current_task": null,

  "hitl": {
    "pending": false,
    "questions": [],
    "answers": {}
  },

  "last_error": null
}
```

---

# 6. Как оркестратор работает со state

Оркестратор не хранит историю в state. Он делает так:

1. Загрузил `cases.state`.
2. Собрал компактный контекст для LLM.
3. LLM выбрал действие.
4. Оркестратор обновил state.
5. Записал event.
6. Вызвал агента или HITL.
7. После агента слил результат в `state.data`.
8. Повторил цикл.

Пример компактного контекста для Decision LLM:

```json
{
  "goal": "Обновить SCHEDULE по новым датам запуска из Excel",
  "artifacts_present": [
    "excel",
    "schedule_source",
    "surface",
    "trajectory"
  ],
  "data_present": {
    "excel": false,
    "calc": false,
    "schedule": false
  },
  "plan": [
    {
      "id": "extract_excel",
      "status": "pending"
    },
    {
      "id": "calc_perf_top",
      "status": "pending"
    },
    {
      "id": "build_schedule",
      "status": "pending"
    }
  ],
  "current_task": null,
  "hitl_pending": false
}
```

Не передавайте LLM весь state, если он большой. Передавайте только то, что нужно для выбора следующего шага.

---

# 7. Реестр агентов

Для MVP можно хранить в Postgres или даже в JSON внутри промпта. Для масштабируемости лучше таблица.

```sql
CREATE TABLE agent_registry (
    agent_id TEXT PRIMARY KEY,
    title TEXT,
    when_to_use TEXT,
    input_required JSONB,
    output_provides JSONB
);
```

Пример:

```json
[
  {
    "agent_id": "excel_extractor",
    "title": "Excel Extractor",
    "when_to_use": "Если есть Excel-файл и нужно извлечь скважины, даты, дебиты, управления",
    "input_required": ["excel"],
    "output_provides": ["excel_table", "normalized_rows"]
  },
  {
    "agent_id": "schedule_builder",
    "title": "Schedule Builder",
    "when_to_use": "Если есть source SCHEDULE и извлечённые данные, нужно собрать изменения SCHEDULE",
    "input_required": ["schedule_source", "excel_data"],
    "output_provides": ["schedule_out", "diff"]
  },
  {
    "agent_id": "calculation_agent",
    "title": "Calculation Agent",
    "when_to_use": "Если есть структурная поверхность и траектория, нужно найти пересечение и начало интервала перфорации",
    "input_required": ["surface", "trajectory"],
    "output_provides": ["top_perforation_md"]
  }
]
```

---

# 8. Decision LLM: контракт ответа

Decision LLM должен возвращать строго один из трех типов действий.

## 8.1. Вызвать агента

```json
{
  "status_message": "Сбор данных по датам ввода из Excel",
  "plan_update": [
    {
      "id": "extract_excel",
      "status": "running"
    }
  ],
  "action": {
    "type": "call_agent",
    "agent_id": "excel_extractor",
    "task_id": "TASK-001",
    "handoff_message": "Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты. Если формат дат неоднозначный — верни needs_input.",
    "task": {
      "excel_artifact": "files/wells_2026.xlsx",
      "fields": [
        "well",
        "start_date",
        "orat",
        "wrat",
        "bhp"
      ]
    }
  }
}
```

## 8.2. Запросить данные у пользователя

```json
{
  "status_message": "Нужно уточнение по колонке даты",
  "action": {
    "type": "ask_user",
    "question_id": "Q-001",
    "question": "Колонка 'Date' — это дата запуска или дата окончания?",
    "options": [
      "запуск",
      "окончание"
    ]
  }
}
```

## 8.3. Завершить задачу

```json
{
  "status_message": "SCHEDULE собран, задача завершена",
  "action": {
    "type": "finish",
    "result": {
      "schedule_out": "out/schedule_v2.inc",
      "diff": "out/schedule_v2.diff"
    }
  }
}
```

---

# 9. Минимальная концептуальная схема оркестратора

В n8n можно делать много узлов, но концептуально оркестратор состоит из 5 блоков.

```text
1. Trigger / Resume
   ↓
2. Load State
   ↓
3. Decision LLM
   ↓
4. Action Router
   ↓
5. Persist State + Emit Events
   ↓
   ├─ call_agent → Agent Runner → loop
   ├─ ask_user → waiting_user
   └─ finish → done
```

Если нужно минимизировать ноды, объединяйте:

- Load State + preparation → один Code node;
- Decision parsing + event writing + state update → один Code node;
- Action Router → Switch node;
- Agent call → один Execute Workflow / HTTP node.

Практичный минимальный набор нод:

```text
Webhook Trigger
Postgres: load/insert case
Code: build decision context + insert execution_id
OpenAI/Qwen: decision
Code: parse decision, update state, write events
Switch: action type
HTTP/Execute Workflow: agent runner
Code: merge agent result, update state, write events
IF: continue / waiting_user / finish
```

Для отладки лучше делать **один execution = один шаг оркестратора**. Тогда error trace от n8n будет точным: сразу видно, на каком шаге упало.

Если нужно продолжать автоматически, последний узел может вызвать тот же webhook:

```text
POST /orchestrator/run
{
  "case_id": "CASE-001"
}
```

---

# 10. Единый контракт задачи агента

Все агенты принимают задачу в одном формате.

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-001",
  "agent_id": "excel_extractor",
  "objective": "Извлечь данные из Excel",
  "handoff_message": "Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты.",
  "inputs": {},
  "context": {},
  "constraints": {}
}
```

---

# 11. Единый контракт ответа агента

Все агенты отвечают в одном формате.

```json
{
  "task_id": "TASK-001",
  "status": "completed",
  "message": "Извлечено 42 строки по 3 скважинам",
  "data": {},
  "artifacts": {},
  "issues": [],
  "assumptions": [],
  "requests": []
}
```

Статусы:

```text
completed
needs_input
failed
```

Если агенту чего-то не хватает:

```json
{
  "task_id": "TASK-002",
  "status": "needs_input",
  "message": "Не могу однозначно определить формат даты",
  "data": {},
  "artifacts": {},
  "issues": [
    {
      "type": "ambiguous_date_format",
      "detail": "В Excel есть даты 01.02.2026 и 02/03/2026"
    }
  ],
  "assumptions": [],
  "requests": [
    {
      "question_id": "Q-002",
      "question": "Какой формат даты использовать?",
      "options": [
        "DD.MM.YYYY",
        "MM/DD/YYYY"
      ]
    }
  ]
}
```

---

# 12. Агент 1: Excel Extractor

## 12.1. Ответственность

Excel Extractor отвечает только за:

- прочитать Excel;
- найти таблицы;
- извлечь колонки;
- нормализовать даты/числа;
- вернуть структурированные данные;
- если есть неоднозначность — вернуть `needs_input`.

Он не редактирует SCHEDULE.

---

## 12.2. Схема агента

```text
AgentTask
   ↓
agent.accepted event
   ↓
excel-service: detect tables
   ↓
excel-service: normalize rows
   ↓
ambiguous?
   ├─ yes → needs_input
   └─ no → result
```

---

## 12.3. Пример задачи

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-001",
  "agent_id": "excel_extractor",
  "objective": "Извлечь данные по скважинам и датам запуска из Excel",
  "handoff_message": "Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты. Если колонки неоднозначные — верни needs_input.",
  "inputs": {
    "excel_artifact": "files/wells_2026.xlsx",
    "fields": [
      "well",
      "start_date",
      "orat",
      "wrat",
      "bhp"
    ]
  },
  "context": {},
  "constraints": {
    "date_format": "auto",
    "units": "auto"
  }
}
```

---

## 12.4. Пример результата

```json
{
  "task_id": "TASK-001",
  "status": "completed",
  "message": "Извлечено 42 строки по 3 скважинам",
  "data": {
    "table_id": "TBL-001",
    "rows_ref": "db://excel_rows?table_id=TBL-001",
    "wells": [
      "101",
      "102",
      "103"
    ],
    "fields_mapped": {
      "well": "well",
      "start_date": "start_date",
      "orat": "orat",
      "wrat": "wrat",
      "bhp": "bhp"
    }
  },
  "artifacts": {},
  "issues": [],
  "assumptions": [
    {
      "id": "ASM-001",
      "text": "Даты из Excel интерпретируются как даты запуска",
      "impact": "medium"
    }
  ],
  "requests": []
}
```

---

# 13. Агент 2: Schedule Builder

## 13.1. Ответственность

Schedule Builder отвечает за:

- понять, какие изменения надо внести в SCHEDULE;
- использовать объектную модель ключевых слов;
- сформировать задачу для `schedule-builder-service`;
- получить новый SCHEDULE / include / diff;
- вернуть результат или `needs_input`.

Он не пишет текст SCHEDULE сам напрямую. Он работает через сервис.

---

## 13.2. Объектная модель ключевых слов внутри сервиса

Внутри `schedule-builder-service` должна быть модель:

```python
class Keyword:
    code: str
    section: str
    description: str
    fields: list[Field]
    methods: list[Method]
    examples: list[str]
    constraints: dict


class Field:
    name: str
    type: str
    unit: str | None
    required: bool
    description: str


class Method:
    name: str
    description: str
    input_schema: dict
```

Пример:

```json
{
  "keyword": "WCONPROD",
  "section": "SCHEDULE",
  "description": "Управление добывающей скважиной",
  "fields": [
    {
      "name": "well",
      "type": "string",
      "required": true
    },
    {
      "name": "status",
      "type": "string",
      "required": false
    },
    {
      "name": "ORAT",
      "type": "number",
      "unit": "m3/d",
      "required": false
    },
    {
      "name": "WRAT",
      "type": "number",
      "unit": "m3/d",
      "required": false
    },
    {
      "name": "BHP",
      "type": "number",
      "unit": "bar",
      "required": false
    }
  ],
  "methods": [
    "create_record",
    "update_field",
    "validate_record"
  ]
}
```

---

## 13.3. Endpoints schedule-builder-service

Минимальный набор:

```text
GET  /keywords
GET  /keywords/{keyword}
GET  /keywords/search?intent=...
POST /keywords/{keyword}/prepare
POST /build
POST /apply
POST /diff
```

Пример:

```text
GET /keywords/WCONPROD
```

Ответ:

```json
{
  "keyword": "WCONPROD",
  "section": "SCHEDULE",
  "description": "Управление добывающей скважиной",
  "fields": [],
  "methods": [
    "create_record",
    "update_field",
    "validate_record"
  ]
}
```

---

## 13.4. Схема агента

```text
AgentTask
   ↓
agent.accepted event
   ↓
определить intent изменений
   ↓
schedule-builder-service: найти подходящие keywords
   ↓
schedule-builder-service: получить keyword objects
   ↓
сформировать build task
   ↓
schedule-builder-service: build/apply
   ↓
result / needs_input / failed
```

---

## 13.5. Пример задачи

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-002",
  "agent_id": "schedule_builder",
  "objective": "Собрать изменения SCHEDULE по извлечённым данным",
  "handoff_message": "Schedule Builder, вот данные из Excel. Собери изменения DATES и WCONPROD. Если скважины нет в исходном SCHEDULE — не придумывай, верни needs_input.",
  "inputs": {
    "schedule_source": "models/base_schedule.inc",
    "excel_table_id": "TBL-001",
    "rows_ref": "db://excel_rows?table_id=TBL-001"
  },
  "context": {
    "calc": {
      "top_perforation_md": 2456.7
    }
  },
  "constraints": {
    "output_mode": "diff_and_new_file"
  }
}
```

---

## 13.6. Пример результата

```json
{
  "task_id": "TASK-002",
  "status": "completed",
  "message": "SCHEDULE обновлён: изменены DATES и WCONPROD для 3 скважин",
  "data": {
    "changed_keywords": [
      "DATES",
      "WCONPROD"
    ],
    "records_applied": 12
  },
  "artifacts": {
    "schedule_out": "out/schedule_v2.inc",
    "diff": "out/schedule_v2.diff"
  },
  "issues": [],
  "assumptions": [],
  "requests": []
}
```

Если скважина не найдена:

```json
{
  "task_id": "TASK-002",
  "status": "needs_input",
  "message": "Скважина '105' есть в Excel, но не найдена в SCHEDULE",
  "data": {},
  "artifacts": {},
  "issues": [
    {
      "type": "well_not_found",
      "well": "105",
      "source_row": 15
    }
  ],
  "assumptions": [],
  "requests": [
    {
      "question_id": "Q-003",
      "question": "Скважина '105' не найдена в SCHEDULE. Пропустить или добавить новую?",
      "options": [
        "skip",
        "add_new"
      ]
    }
  ]
}
```

---

# 14. Агент 3: Calculation Agent

## 14.1. Ответственность

Calculation Agent отвечает за специфичные расчеты.

Первый кейс:

> Найти начало интервала перфорации по пересечению структурной поверхности и траектории скважины.

Он не редактирует SCHEDULE и не читает Excel, если это не нужно для расчета.

---

## 14.2. Схема агента

```text
AgentTask
   ↓
agent.accepted event
   ↓
проверить наличие surface и trajectory
   ↓
calc-service: загрузить surface
   ↓
calc-service: загрузить trajectory
   ↓
calc-service: intersection
   ↓
если пересечение одно → result
если несколько → needs_input или список
если нет → failed / needs_input
```

---

## 14.3. Пример задачи

```json
{
  "case_id": "CASE-001",
  "task_id": "TASK-003",
  "agent_id": "calculation_agent",
  "objective": "Найти начало интервала перфорации по пересечению поверхности и траектории",
  "handoff_message": "Calc Agent, есть структурная поверхность и траектория. Найди первую точку пересечения и верни MD начала перфорации. Если пересечений несколько — верни список и уточни выбор.",
  "inputs": {
    "surface_artifact": "geo/struct_surface.xyz",
    "trajectory_artifact": "well/trajectory_101.csv",
    "well": "101"
  },
  "context": {
    "depth_type": "TVD",
    "units": "metric"
  },
  "constraints": {
    "choose": "first_intersection_from_wellhead"
  }
}
```

---

## 14.4. Пример результата

```json
{
  "task_id": "TASK-003",
  "status": "completed",
  "message": "Найдено пересечение. Начало интервала перфорации: MD 2456.7",
  "data": {
    "well": "101",
    "top_perforation_md": 2456.7,
    "top_perforation_tvd": 2310.2,
    "intersection_count": 1,
    "point": {
      "x": 412567.8,
      "y": 6789123.4,
      "tvd": 2310.2
    }
  },
  "artifacts": {},
  "issues": [],
  "assumptions": [
    {
      "id": "ASM-002",
      "text": "Поверхность задана в TVD",
      "impact": "high"
    }
  ],
  "requests": []
}
```

Если пересечений несколько:

```json
{
  "task_id": "TASK-003",
  "status": "needs_input",
  "message": "Найдено несколько пересечений поверхности и траектории",
  "data": {
    "well": "101",
    "intersections": [
      {
        "md": 2456.7,
        "tvd": 2310.2
      },
      {
        "md": 2610.4,
        "tvd": 2488.9
      }
    ]
  },
  "artifacts": {},
  "issues": [],
  "assumptions": [],
  "requests": [
    {
      "question_id": "Q-004",
      "question": "Найдено 2 пересечения. Какое использовать как начало перфорации?",
      "options": [
        "2456.7",
        "2610.4"
      ]
    }
  ]
}
```

---

# 15. Как агенты пишут события

Есть два практичных варианта.

## Вариант 1: события пишет оркестратор

Тогда агент просто возвращает результат, а оркестратор пишет:

```text
agent.handoff
agent.result
agent.failed
```

Плюсы: проще агенты.  
Минусы: меньше realtime внутри долгой работы агента.

---

## Вариант 2: события пишет и агент

Тогда агент пишет:

```text
agent.accepted
agent.progress
agent.result
agent.failed
```

Плюсы: лучший realtime UI.  
Минусы: агенты должны уметь писать в `events`.

Для вашей задачи лучше вариант 2.

Минимальный набор:

```text
orchestrator пишет:
- case.created
- orchestrator.status
- orchestrator.decision
- agent.handoff
- hitl.request
- case.finished

agent пишет:
- agent.accepted
- agent.progress
- agent.result
- agent.failed
```

---

# 16. HITL flow

HITL нужен только когда:

- не хватает данных;
- есть неоднозначность;
- нужно подтвердить допущение;
- есть конфликт.

Схема:

```text
Agent возвращает status = needs_input
   ↓
Orchestrator пишет hitl.request
   ↓
case.status = waiting_user
   ↓
UI показывает вопрос
   ↓
User отвечает
   ↓
POST /cases/{case_id}/answer
   ↓
Orchestrator обновляет state.hitl.answers
   ↓
Orchestrator продолжает цикл
```

Пример `hitl.request`:

```json
{
  "case_id": "CASE-001",
  "kind": "hitl.request",
  "actor": "orchestrator",
  "status": "waiting_user",
  "status_message": "Нужно уточнение по скважине 105",
  "handoff_message": null,
  "payload": {
    "question_id": "Q-003",
    "question": "Скважина '105' не найдена в SCHEDULE. Пропустить или добавить новую?",
    "options": [
      "skip",
      "add_new"
    ],
    "source_agent": "schedule_builder"
  }
}
```

Ответ пользователя:

```json
{
  "case_id": "CASE-001",
  "kind": "hitl.answered",
  "actor": "user",
  "status": "answered",
  "status_message": "Пользователь ответил: add_new",
  "handoff_message": null,
  "payload": {
    "question_id": "Q-003",
    "answer": "add_new"
  }
}
```

---

# 17. Error trace: нативный n8n Error Workflow

Важное разделение:

## Бизнес-ошибка агента

Например:

```json
{
  "status": "failed",
  "message": "Не найден keyword XYZ"
}
```

Это событие:

```text
agent.failed
```

Оно не обязательно означает, что n8n-нода упала.

---

## Node-level ошибка

Например:

- упал OpenAI node;
- timeout;
- Code node выбросил exception;
- HTTP Request вернул 500 и не был обработан;
- сломался JSON parsing.

Это уже `error_traces`.

---

# 18. Схема Error Workflow

Отдельный workflow:

```text
Error Trigger
   ↓
Postgres: найти case_id по execution_id
   ↓
Postgres: insert error_traces
   ↓
Postgres: insert events kind = system.node_error
   ↓
опционально Telegram / Discord / email
```

Пример обработки Error Trigger:

```json
{
  "execution_id": "{{ $json.execution.id }}",
  "workflow_name": "{{ $json.execution.workflow.name }}",
  "node_name": "{{ $json.execution.lastNodeExecuted }}",
  "error_message": "{{ $json.execution.error.message }}",
  "error_type": "{{ $json.execution.error.name }}",
  "stack": "{{ $json.execution.error.stack }}"
}
```

Сначала находите `case_id`:

```sql
SELECT case_id
FROM executions
WHERE execution_id = $1;
```

Потом пишете error:

```sql
INSERT INTO error_traces (
    case_id,
    execution_id,
    workflow_name,
    node_name,
    error_message,
    error_type,
    stack
)
VALUES (
    $1,
    $2,
    $3,
    $4,
    $5,
    $6,
    $7
);
```

Потом пишете событие для UI:

```json
{
  "case_id": "CASE-001",
  "kind": "system.node_error",
  "actor": "n8n",
  "status": "error",
  "status_message": "Упал узел Decision LLM",
  "handoff_message": null,
  "payload": {
    "error_id": 123,
    "execution_id": "999",
    "node_name": "Decision LLM"
  }
}
```

UI показывает такие ошибки отдельным красным блоком.

---

# 19. Пример полного event timeline

```json
[
  {
    "kind": "case.created",
    "status_message": "Принял задачу: обновить SCHEDULE по новым датам из Excel",
    "handoff_message": null
  },
  {
    "kind": "orchestrator.status",
    "status_message": "Нужно извлечь данные из Excel",
    "handoff_message": null
  },
  {
    "kind": "agent.handoff",
    "agent_id": "excel_extractor",
    "status_message": "Сбор данных по датам ввода из Excel",
    "handoff_message": "Excel Extractor, вот файл. Достань скважины, даты запуска и дебиты."
  },
  {
    "kind": "agent.accepted",
    "agent_id": "excel_extractor",
    "status_message": "Разбираю Excel",
    "handoff_message": null
  },
  {
    "kind": "agent.result",
    "agent_id": "excel_extractor",
    "status_message": "Извлечены даты ввода по 3 скважинам",
    "handoff_message": null
  },
  {
    "kind": "orchestrator.status",
    "status_message": "Есть поверхность и траектория, считаю начало перфорации",
    "handoff_message": null
  },
  {
    "kind": "agent.handoff",
    "agent_id": "calculation_agent",
    "status_message": "Расчёт точки пересечения поверхности и траектории",
    "handoff_message": "Calc Agent, найди первую точку пересечения поверхности и траектории, верни MD начала перфорации."
  },
  {
    "kind": "agent.result",
    "agent_id": "calculation_agent",
    "status_message": "Найдено начало перфорации: MD 2456.7",
    "handoff_message": null
  },
  {
    "kind": "agent.handoff",
    "agent_id": "schedule_builder",
    "status_message": "Формирование изменений SCHEDULE",
    "handoff_message": "Schedule Builder, вот данные из Excel и рассчитанный MD перфорации. Собери изменения DATES и WCONPROD."
  },
  {
    "kind": "agent.result",
    "agent_id": "schedule_builder",
    "status_message": "SCHEDULE обновлён, diff создан",
    "handoff_message": null
  },
  {
    "kind": "case.finished",
    "status_message": "Задача завершена",
    "handoff_message": null
  }
]
```

---

# 20. API для UI

Минимальный набор:

```text
GET  /cases/{case_id}/state
GET  /cases/{case_id}/events?after_seq=0
GET  /cases/{case_id}/errors
POST /cases/{case_id}/answer
POST /cases/{case_id}/run
```

Для realtime можно:

1. MVP: polling каждые 1–2 секунды.
2. Лучше: FastAPI SSE endpoint.
3. Еще лучше: Postgres `LISTEN/NOTIFY`.

Пример SSE:

```text
GET /cases/{case_id}/stream
```

---

# 21. Как должен выглядеть Decision-промпт

Короткий и жесткий:

```text
Ты оркестратор инженерной задачи.

Цель:
{goal}

Текущее состояние:
{decision_context}

Доступные агенты:
{agent_registry}

Ты должен выбрать одно действие:
1. call_agent
2. ask_user
3. finish

Правила:
- Если не хватает данных, выбери ask_user.
- Если следующий шаг очевиден, выбери call_agent.
- Если задача выполнена, выбери finish.
- Не придумывай данные.
- Возвращай только JSON.
```

---

# 22. Минимальные правила масштабирования

## 22.1. Новый агент добавляется без переписывания оркестратора

Добавляете:

```text
1. FastAPI сервис агента
2. Запись в agent_registry
3. Контракт AgentTask / AgentResult
```

Оркестратор сам начнет выбирать его по `when_to_use`.

---

## 22.2. Агент не должен быть болтливым

Агент возвращает:

```text
status
message
data
artifacts
issues
assumptions
requests
```

Никаких свободных ответов.

---

## 22.3. Каждый handoff — событие

Перед вызовом агента:

```text
agent.handoff
```

При приеме:

```text
agent.accepted
```

При результате:

```text
agent.result
```

---

## 22.4. Бизнес-ошибки и ошибки нод分开

Бизнес-ошибка агента:

```text
agent.failed
```

Ошибка ноды n8n:

```text
error_traces + system.node_error
```

Не смешивайте их.

---

## 22.5. State — только факты, events — история

State:

```text
что сейчас есть
какие артефакты есть
какие данные получены
какой текущий task
есть ли HITL
```

Events:

```text
что происходило
кто кому передал
какие были сообщения
где упало
```

---

# 23. Итоговая формула

```text
Оркестратор:
  state + goal + agent_registry
  → LLM выбирает агента
  → пишет handoff event
  → вызывает агента

Агент:
  принимает AgentTask
  → пишет accepted
  → делает работу через свой FastAPI-сервис
  → возвращает AgentResult
  → пишет result / needs_input / failed

UI:
  читает events realtime
  → видит status_message
  → видит handoff_message
  → отвечает на HITL

n8n Error Workflow:
  ловит только node-level ошибки
  → пишет error_traces
  → пишет system.node_error
```

Это дает минимальный набор нод, нормальную отладку, realtime UI и масштабируемость без хардкода.