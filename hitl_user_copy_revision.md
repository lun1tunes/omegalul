# Ревизия user-facing copy / HITL

## Краткий вывод

Кадр с `INCLUDE_NOT_FOUND` и английской строкой — не баг перевода одной фразы. Это **схлопывание двух каналов**: машинный finding (код, path, hash) показывают инженеру как вопрос HITL.

Политика уже записана и частично соблюдается LLM-слоем:

- `n8n/templates/orchestrator-instruction.template.md` § Human-facing copy
- `specialist-workflow-instruction.template.md`
- Planner schema поле `user_message` («1–3 коротких русских предложения»)

Детерминированные Code-узлы эту политику **не исполняют**. `GATE_RESULT` не ставит `user_message`, берёт английский fallback `summary`, а вопросы собирает как `f.message || f.code`. Оркестратор кладёт это в `gate.reason` / `questions`. Activity рисует as-is плюс шум «обязательно».

Система для пользователя — не «перевести все строки». Это **обязательный human-ask рядом с каждым hard-stop**: что случилось, что приложить/написать, одним действием «Ответить». Коды остаются во внутреннем контуре (CAS, trace, pytest).

Код в этом документе не менялся.

## Два канала (контракт)

| Канал | Куда идёт | Форма | Аудитория |
|---|---|---|---|
| **Finding** | CAS `compact_data.findings`, `reason_codes`, smoke-тесты, логи | `{code, severity, file_ref, path, keyword, …}` | система, регрессия, отладка |
| **Human ask** | `user_message`, `human_request.questions[].text`, `gate.reason`, brief ленты | русский текст: ситуация + действие; имена файлов и keyword — латиницей | инженер в Activity |

Правило: **hard-stop без human ask запрещён**. Либо проверка эмитит оба поля, либо единый **copy compiler** строит ask из `code` + слотов (`path`, `file_ref`, `keyword`, `entity`). Показ `CODE_NAME` как единственного текста вопроса — дефект, как parse failure.

Латиница допустима только как **имя объекта**: `WELLS.INC`, `INCLUDE`, `WCONPROD`, `ORAT`. Английский filler (`pipeline`, `controlled decision`, `required evidence is missing`, `Resolve the finding`) в русской карточке запрещён.

## Как сломался кадр со скрина

Цепочка для `INCLUDE_NOT_FOUND`:

1. Lossless analysis (`schedule_lossless_runtime.py`) пушит `{code:'INCLUDE_NOT_FOUND', severity:'error', file_ref, path, target_file_ref}` — **без `message`**. Это правильно для машины: слоты есть.
2. Decoder может добавить ещё такие же коды по `file_ref` (`schedule_baseline_decoder.py`).
3. `GATE_RESULT` (`schedule_pipeline.py`) держит до **трёх копий одного code**, вопросы = `f.message || f.code` → три раза `INCLUDE_NOT_FOUND`.
4. `summary` fallback: английское `'SCHEDULE pipeline requires additional evidence or a controlled decision.'`. Поля `user_message` нет.
5. `BUILD_DIRECT_GATE` берёт `user_message || summary` → английский `gate.reason`.
6. Activity: бейдж «Нужны данные» (локальный label kind), reason as-is, вопросы as-is, под каждым «обязательно», потому что `required:true`.

Инженер не видит: какой файл сослался, какой path не приложен, что делать (прикрепить `.INC` и нажать «Ответить»). Видит внутренний enum и английский stub.

Тот же паттерн повторяется на intake, Excel adapter, invalid packet, planner fallback `reason`.

## P0 — канал человека не собран

### 1. `GATE_RESULT` — главный компилятор HITL для Builder, и он компилирует в код

Источник: `n8n/templates/schedule_pipeline.py` `GATE_RESULT`.

- нет `user_message`;
- `summary` = `x.summary || rationale || finding.note ||` английский stub;
- вопросы из gaps: английский `'required evidence is missing'`;
- иначе вопросы из findings: `'Resolve the SCHEDULE pipeline finding.'` или сырой `code`;
- дедуп слабый: один code до трёх раз.

Это единственная точка, через которую почти все hard-stop Builder (baseline, decode, merge, commissioning) становятся карточкой. Исправлять по одному finding в lossless недостаточно: без компилятора следующий код (`INCLUDE_CYCLE`, `BASELINE_TEXT_REQUIRED`, `ROOT_PATH_UNSAFE`) снова вылезет как SNAKE_CASE.

### 2. Оркестратор предпочитает log-`summary` человеческому `user_message`, которого нет

Источник: `generate_universal_engineering_workflows.py` `BUILD_DIRECT_GATE`.

`reason = user_message || summary || 'Нужно решение человека.'`

Пока specialist не заполняет `user_message`, в UI всегда уйдёт `summary`. Для FINAL_RESULT (выпуск) `user_message` уже русский. Для GATE_RESULT — нет. Политика instruction templates здесь не действует: узел детерминированный.

### 3. Проверки эмитят слоты, но не human ask

Источники: `schedule_lossless_runtime.py` (`INCLUDE_NOT_FOUND` с `file_ref`/`path`), `schedule_baseline_decoder.py` (тот же code без path), десятки `findings.push({code:...})` без `message`.

Машинный контур полный. Человеческий — пустой. Copy compiler должен читать слоты: «в `{file_ref}` есть INCLUDE `{path}`, файла нет в пакете — прикрепите его».

### 4. Intake уже имеет каталог фраз — на английском и про контракт, не про действие инженера

Источник: `schedule_intake_runtime.py` map `code → question`.

Примеры: `'Use the governed schedule_build_request/v1 handoff.'`, `'Set preservation_policy to preserve_unmentioned.'`, `'Resolve intake finding ${f.code}.'`

Это сообщения для разработчика workflow, не для пользователя Activity. Даже `BASELINE_REQUIRED` должно звучать: «Для REVISE нужен предыдущий schedule. Прикрепите корневой `.inc` / `.data`.», а не «Attach the previous SCHEDULE text for REVISE.»

### 5. Excel adapter HITL на английском и про binary field names

Источник: `generate_universal_engineering_workflows.py` `BUILD_EXCEL_ADAPTER_GATE`.

«Upload the .xlsx or .xls workbook in binary field file» — поле формы n8n, не Activity. Пользователь должен услышать: «Прикрепите книгу Excel (.xlsx) к ответу.»

Clarification: `'Additional Excel extraction information is required.'` — снова stub.

### 6. Activity — last-mile без защиты

Источник: `mas-activity-service/static/app.js` `renderGate`.

Лента уже прячет английский muted text (`shouldShowMutedText` + кириллица). Карточка гейта **не** фильтрует: английский reason и `INCLUDE_NOT_FOUND` проходят. «обязательно» рисуется на каждом вопросе, хотя HITL и так обязателен (одна кнопка «Ответить»). Kind локализован, тело — нет. Last-mile не заменяет компилятор, но обязан не показывать голый CODE и английский-only reason.

## P1 — та же дыра в соседних узлах

### 7. Planner / APPLY_PLAN английские `reason`

`'Planner returned invalid structure.'`, `'Planner could not produce a complete specialist_packet…'`. Попадают в `gate.reason`, если нет `user_message`.

### 8. `INVALID_RESULT` и RAG-gate

`'Load active expert keyword_instruction blocks… into schedule_mvp.'` — admin-задача, но всё равно должна быть по-русски и с ролью: «В базе знаний нет полной инструкции/схемы keyword. Загрузите корпус через Knowledge.»

### 9. Дубли вопросов

Три одинаковых `INCLUDE_NOT_FOUND` — баг дедупа (`compactFindings` держит до 3 копий code) плюс отсутствие ключа `(code, path, file_ref)`. Один path — одна строка. Несколько path — список имён файлов, один общий `user_message`.

### 10. Kind vs действие

Бейдж «Нужны данные» верен. Английский reason его обесценивает. Kind — таксономия оркестратора (`needs_input`). Карточка должна объяснять **этот** stop, а не повторять kind другим языком.

## Целевая система

```text
Check (lossless / intake / adapter / verifier)
  → finding {code, slots}          // машина
  → copy compiler (один модуль)
       catalog[code](slots) → {user_message, questions[]}
  → specialist_result.user_message + human_request
  → BUILD_DIRECT_GATE: reason = user_message (summary только если русский)
  → Activity: карточка + композер «Ответить» (+ файлы)
```

**Copy compiler** — один JS-хелпер в templates (подключается в GATE_RESULT, intake, excel gate, при желании last-mile Activity). Каталог: code → шаблон русского ask. Слоты подставляются. Нет шаблона → generic: «Проверка остановила задачу. Напишите, что сделать, или приложите недостающие файлы.» + code только в `details`/title для отладки, не как тело вопроса.

**Карточка гейта (контракт UI):**

- одна фраза `reason` = что случилось + что сделать;
- список уникальных asks (файлы, поля, решение keep/remove) — не enum;
- без «обязательно», если все пункты обязательны;
- без сырого `gate_id` / `awaiting_human` (уже убрано);
- одно действие: Ответить (уже так).

**Пример INCLUDE_NOT_FOUND:**

- reason: «В пакете нет тел файлов, на которые ссылается INCLUDE. Прикрепите недостающие `.inc` и нажмите Ответить.»
- items: «Нет файла `path`, на него ссылается `file_ref`.»
- внутренний finding code остаётся в compact_data для тестов.

**Аудитории:**

- инженер (HITL) — ситуация и действие;
- админ (RAG/allowlist) — кто и куда загружает; всё равно русский;
- лог/CAS — code.

**Что не переводить:** keyword, имена файлов, идентификаторы скважин, `act_`/`eng_` ids.

## Что оставить

- Finding codes и pytest на `INCLUDE_NOT_FOUND` и граф INCLUDE.
- Bounded packets, version guard, HITL kind taxonomy.
- Политика в instruction templates — её нужно **исполнять** в Code nodes, а не дублировать новой прозой в LLM-only.
- Латинские keyword/filenames в русской фразе.

Не являются решением: точечный `if (code==='INCLUDE_NOT_FOUND')` только в Activity; перевод stub на русский без слотов (опять три одинаковые строки без имён файлов).

## Приоритет

1. **P0:** copy compiler + каталог для schedule findings (INCLUDE_*, BASELINE_*, ROOT_PATH_*, PACKAGE_*) в `GATE_RESULT`; всегда `user_message`; дедуп по `(code, path, file_ref)`.
2. **P0:** `BUILD_DIRECT_GATE` не берёт английский `summary`; Activity last-mile не рисует голый CODE / English-only reason.
3. **P0:** intake catalog и Excel adapter — те же русские asks «что сделать», не binary field / contract names.
4. **P1:** planner fallback reasons; INVALID_RESULT / RAG; убрать «обязательно»-шум.
5. **P1:** decoder `INCLUDE_NOT_FOUND` должен нести `path`, не только `file_ref`, чтобы ask был конкретным.

## Матрица приёмки

Карточка HITL для живого инженера:

- есть кириллица в `reason`;
- нет вопроса, чей текст целиком `^[A-Z][A-Z0-9_]{3,}$`;
- для missing INCLUDE видны **имена файлов**, не три раза один code;
- сказано, что делать (прикрепить / написать), и это стыкуется с одной кнопкой «Ответить»;
- CAS/smoke по-прежнему видят `code === 'INCLUDE_NOT_FOUND'`.

Кейсы: missing INCLUDE (этот скрин); нет baseline на REVISE; нет Excel workbook; intake без дат; RAG пустой; выпуск готов (уже русский FINAL_RESULT).

**Итог:** Activity уже умеет быть чатом для человека (одна кнопка, вложения). Контур останова Builder всё ещё говорит на языке workflow. Граница исправления — не «локализовать UI», а **не выпускать HITL-gate без human ask**. Finding остаётся машине; инженеру — фраза и действие.
