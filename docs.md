# NOVATEK RE MASter

Система собирает или правит файл `SCHEDULE` (`.INC`) для tNavigator / ECLIPSE по задаче гидродинамика. Вы пишете цель по-русски, прикладываете Excel и исходный schedule — система читает таблицы, при необходимости спрашивает вас и отдаёт новый `.INC` с diff.

Этот документ — только про развёртывание и работу. Корпоративный n8n вы трогаете из браузера (Import from File, Credentials, Settings). Сервисы крутятся на вашей Windows.

---

## Как это устроено

Вы общаетесь с **Activity** — это чат на вашем компьютере. Activity зовёт **оркестратор** в корпоративном n8n. Оркестратор решает, кого вызвать, и отдаёт работу агентам. Агенты — тоже workflows в n8n: модель выбирает инструмент, а считает уже Python на Windows.

```mermaid
flowchart TB
  You[Вы] --> Activity["Activity :8200<br/>чат, файлы, результаты"]

  subgraph win [Ваша Windows]
    Activity
    ExcelSvc["Excel Tools :8000<br/>читает книги"]
    SchedSvc["Schedule Builder :8090<br/>правит .INC"]
    MathSvc["Math :8100"]
  end

  subgraph n8n [Корпоративный n8n 2.30.8]
    Orch[Оркестратор]
    ExcelA[Агент Excel]
    SchedA[Агент Schedule]
    Proxy[Прокси к Postgres]
    RAG[База знаний]
    Cfg[Адреса и лимиты]
    Model[Ваша модель]
  end

  PG[(Postgres)]

  Activity -->|создать задачу / ответ| Orch
  Activity --> Proxy
  Orch --> Model
  Orch --> ExcelA
  Orch --> SchedA
  Orch --> RAG
  Orch --> Cfg
  ExcelA --> ExcelSvc
  SchedA --> SchedSvc
  ExcelA --> Model
  SchedA --> Model
  ExcelSvc --> Activity
  SchedSvc --> Activity
  Proxy --> PG
  Orch --> PG
  RAG --> PG
```

Ещё в n8n: **Error — MAS Node Traces** (ошибки узлов попадают в лог задачи) и **форма Health Check** (проверка, что всё связано).

Как идёт одна задача:

1. Вы создаёте задачу в Activity: текст цели, Excel, исходный `.INC` и при необходимости INCLUDE.
2. Activity будит оркестратор. Дальше он сам делает шаги, пока не спросит вас или не закончит.
3. Оркестратор смотрит цель, файлы и список доступных агентов, затем либо зовёт агента, либо задаёт вопрос, либо завершает.
4. Агент Excel достаёт факты из книги. Агент Schedule применяет их к исходному файлу и собирает новый `.INC`. Сами строки `WELSPECS` / `WCONPROD` вы не пишете — только инженерные факты (группа, дата, режим, дебит).
5. Если чего-то не хватает, в чате появится вопрос по-русски, часто с кнопками. Можно нажать кнопку, написать фразу своими словами или докинуть файл.
6. Готово: в панели «Результаты» лежат файлы агентов. Скачиваете новый schedule и diff.

Оркестратор не знает агентов «наизусть»: кого звать, он читает из реестра (страница **Агенты** в Activity). Адреса сервисов и модели — в одном месте: workflow **MAS — Runtime Config**, нода `Runtime URLs`. Поле `chat_base_url` — тот же Base URL, что в credential модели; по нему оркестратор ходит за следующим шагом, проверкой «готово» и пониманием свободного ответа на вопрос.

---

## Что уже умеет и чего ещё нет

Умеет: прочитать Excel с датами ввода, параметрами скважин и любой другой таблицей; сдвинуть или убрать скважины в исходном `.INC`; записать именованный набор в keyword schedule по схеме (слово из знаний и каталога, не словарь в коде); спросить вас кнопкой или обычной фразой, если в таблице дырка; отдать новый schedule и diff. Если сервис агента не поднят — задача падает с понятным текстом, её можно перезапустить. Если чего-то не хватает — вопрос в чате, не «напишите строки WELSPECS».

Состав этой сборки — альфа (`VERSION` `0.8.0`, тег `alpha-1`).

Ещё нет: третьего боевого агента (расчёт, выгрузка с кластера) — только Excel и Schedule. Широкий набор живых кейсов (два вопроса от разных агентов, перепривязка и даты в одной задаче, несколько INCLUDE) — после 6.2.

Если оркестратор импортирован до правки thinking: в **Orchestrator — MAS** должны быть HTTP-ноды **Decision chat**, **Verify chat**, **Interpret chat**. У агентов Excel / Schedule / Demo — HTTP-нода **Agent chat**. На все эти ноды — тот же OpenAI-compatible credential; в Runtime Config заполнен `chat_base_url`. Иначе свободный ответ и следующий шаг снова пустеют. Thinking у модели выключен телом запроса (`reasoning.enabled`, `enable_thinking`, `chat_template_kwargs.enable_thinking`), не отдельной нодой языковой модели.

Очередь работ для разработчика — `MAS_REFACTORING_PLAN.md` §2.8.

---

## Что понадобится

- Корпоративный **n8n 2.30.8** — только браузер, без доступа к серверу.
- **PostgreSQL** с расширением `vector` — ставит DBA, вам выдают учётку для credential в n8n.
- **Windows** с Python 3.11–3.13 (pip, без Node.js и без Docker).
- Распакованный проект (четыре каталога сервисов рядом, как в репозитории) и пакет workflows.
- Модель в n8n: credential типа OpenAI-compatible с вашим внутренним Base URL (тот же, что на **Decision chat** и **Agent chat**).

Пакет импорта: в `dist/mas-<версия>.zip` лежат девять JSON, `IMPORT_ORDER.txt` и этот файл. Код сервисов — из распаковки проекта, не из zip.

Если проекта на машине ещё нет: на машине с сетью `python3 scripts/project_pack.py pack`, на работе — `python3 project_pack.py unpack` (секреты в архив не входят).

---

## Развёртывание

Делайте по порядку. Activity запускайте только после шага 5 (прокси должен быть включён).

### 1. Четыре сервиса на Windows

| Сервис | Каталог | Порт | Файл настроек |
|---|---|---|---|
| Excel Tools | `excel-agent-tools` | 8000 | `excel-tools.env` — ключ не нужен |
| Schedule Builder | `schedule-builder-service` | 8090 | `schedule-builder.env` |
| Math | `fastapi-math-service` | 8100 | `math-service.env` |
| Activity | `mas-activity-service` | 8200 | `mas-activity.env` — заполните, но **запустите позже** |

В каждом каталоге:

```bat
setup-windows.bat
copy <сервис>.env.example <сервис>.env
notepad <сервис>.env
start-windows.bat
```

Если n8n на другом компьютере — в env поставьте `*_HOST=0.0.0.0` и откройте порты в firewall.

Проверка трёх сервисов (Activity ещё может молчать): из корня проекта `check-all-windows.bat`. Версия в `/health` должна совпасть с файлом `VERSION`.

В `mas-activity.env` заранее пропишите адреса **как их видит ваша Windows**:

| Переменная | Пример |
|---|---|
| `ORCHESTRATOR_WEBHOOK_URL` | `https://<ваш-n8n>/webhook/mas-orchestrator-step` |
| `CONTROL_PLANE_PROXY_URL` | `https://<ваш-n8n>/webhook/mas-control-plane` |
| `KNOWLEDGE_INGEST_URL` | `https://<ваш-n8n>/webhook/mas-knowledge-ingest` |
| `CONTROL_PLANE_REQUIRED` | `true` |
| `ORCHESTRATOR_AUTH_*` и `CONTROL_PLANE_PROXY_AUTH_*` | те же имя и значение, что Header Auth на webhook'ах в n8n |
| `MAS_ACTIVITY_HOST` | `0.0.0.0`, если n8n не на этом ПК |
| `ACTIVITY_CA_BUNDLE` | PEM корпоративного CA, если n8n по HTTPS |
| `N8N_PUBLIC_URL` | адрес n8n **в браузере** (для ссылок в логе). Часто можно не задавать |

### 2. Импорт в n8n

n8n → **Import from File**, строго по `IMPORT_ORDER.txt`. Пока **ничего не активируйте**.

1. `MAS — Knowledge Ingestion`
2. `MAS — Knowledge Retrieval`
3. `MAS — Runtime Config`
4. `Agent — Schedule Builder`
5. `Agent — Excel Extractor`
6. `Error — MAS Node Traces`
7. `MAS — Control Plane Proxy`
8. `Orchestrator — MAS`
9. `Form — MAS Deployment Health Check`

После импорта у каждого workflow новый id — он виден в адресной строке, когда workflow открыт. Запишите id Excel Extractor и Schedule Builder: понадобятся на шаге 4.

`support/demo-agent.workflow.json` — только если проверяете шаблон агента. Других JSON в `support/` нет.

### 3. Ключи доступа

| Куда | Какой credential |
|---|---|
| **Decision chat**, **Verify chat**, **Interpret chat** в оркестраторе; **Agent chat** у Excel Extractor, Schedule Builder и Demo Agent | Один OpenAI-compatible credential вашей модели |
| Knowledge Ingestion и Retrieval → Embeddings | Отдельный embedding credential, модель `baai/bge-m3`, Dimensions пустое (таблица `tnavigator_schedule_knowledge_v2`) |
| Ingestion, Retrieval, оркестратор, прокси, Error traces → Postgres | Одна учётка Postgres / PGVector (SSL = Disable, если сервер без TLS) |
| Webhook оркестратора, нода **POST continue run**, webhook прокси | Header Auth — те же имя и значение, что в `mas-activity.env` |

В **MAS — Runtime Config** откройте Set `Runtime URLs` и замените адреса на полевые (не оставляйте имена вроде `excel-tools` или `n8n:5678`):

| Поле | Значение |
|---|---|
| `activity_base_url` | `http://<IP-этой-Windows>:8200` |
| `excel_tools_url` | `http://<IP-этой-Windows>:8000` |
| `schedule_service_url` | `http://<IP-этой-Windows>:8090` |
| `math_url` | `http://<IP-этой-Windows>:8100` |
| `orchestrator_step_url` | `https://<ваш-n8n>/webhook/mas-orchestrator-step` — адрес, по которому **n8n достаёт сам себя** |
| `chat_model` | id модели, как в credential |
| `chat_base_url` | Base URL из того же credential, **без** `/chat/completions`. Нужен Decision, Verify, Interpret и Agent chat |
| `chat_extra_params` | JSON-объект поверх сэмплинга и thinking-off (`{}` = значения шаблона). Не кладите сюда ключи и `messages` |
| `mas_version` | строка из файла `VERSION` |
| `max_steps` | обычно `12` |
| `agent_workflow_ids` | пока `{}` — заполните на следующем шаге |

Excel Tools без Header Auth — ключ в Runtime Config и на агенте не нужен.

Проверка связи: на Windows `check-all-windows.bat` / `python scripts/field_check.py` (`/health` четырёх сервисов, Activity `/ready`). В n8n — форма **MAS Deployment Health Check**.

#### Матрица секретов и связей

Секреты живут в Credentials n8n и в `*.env` на Windows. В JSON workflows их нет.

**Credentials n8n → ноды**

| Секрет | Где задаётся | Кто читает | Как проверить |
|---|---|---|---|
| OpenAI-compatible (чат) | Credentials → тот же Base URL, что `chat_base_url` | **Decision chat**, **Verify chat**, **Interpret chat**; **Agent chat** у Excel / Schedule / Demo | Health Check PASS; в Логе `trace.llm` с `finish_reason` не `length`. Корп. vLLM/SGLang: `--enable-auto-tool-choice --tool-call-parser hermes` (иначе цикл агента не получит `tool_calls`). Thinking off: в `trace.llm` нет `think N`, `chat_extra_params` не включает thinking |
| Embeddings `baai/bge-m3`, Dimensions пусто | Отдельный credential (тот же OpenAI-compatible endpoint, что у корпоративного контура) | Knowledge Ingestion, Knowledge Retrieval | «Загрузить в RAG» → ненулевые срезы |
| Postgres / PGVector | Одна учётка (SSL = Disable, если сервер без TLS) | Ingestion, Retrieval, оркестратор, прокси, Error traces | `{"operation":"schema"}` → `ok: true` |
| Header Auth | Имя и значение = `ORCHESTRATOR_AUTH_*` и `CONTROL_PLANE_PROXY_AUTH_*` в `mas-activity.env` | Webhook оркестратора, **POST continue run**, webhook прокси, пробы Health Check | Activity `/ready` 200; `/health` `control_plane_backend=n8n_proxy` |

**Runtime Config (`Runtime URLs`) → потребители**

| Поле | Кто читает | Поле / lab |
|---|---|---|
| `activity_base_url` | агенты (Activity API), оркестратор | IP этой Windows `:8200` / Compose `http://mas-activity:8200` |
| `excel_tools_url` | агент Excel | `:8000` / `http://excel-tools:8000` |
| `schedule_service_url` | агент Schedule | `:8090` / `http://schedule-builder:8090` |
| `math_url` | HTTP-агент Math | `:8100` / `http://math-service:8100` |
| `orchestrator_step_url` | Activity и n8n «сам в себя» | корпоративный URL `/webhook/mas-orchestrator-step` / lab `http://n8n:5678/…` с хоста Windows — тот адрес, с которого n8n достаёт себя |
| `chat_model` | HTTP `/chat/completions` | id как в credential |
| `chat_base_url` | Decision, Verify, Interpret, Agent chat (без `/chat/completions`) | Base URL credential |
| `chat_extra_params` | те же HTTP-чаты (overlay `top_k` / `reasoning` / `enable_thinking`) | `{}` или JSON объекта; секретов нет |
| `mas_version` | Health Check | строка из `VERSION` |
| `max_steps` | оркестратор | обычно `12` |
| `agent_workflow_ids` | оркестратор | id из URL после UI-импорта; lab CLI может оставить `{}` |

**`.env` сервисов (полный список — в `*.env.example`)**

| Файл | Назначение | Поле / lab |
|---|---|---|
| `mas-activity-service/mas-activity.env` | слушатель `:8200`, webhook оркестратора, прокси, TLS, таймауты, ссылки лога | `MAS_ACTIVITY_HOST=0.0.0.0`, корпоративные URL, `ACTIVITY_CA_BUNDLE`; lab — значения из корневого `.env` / Compose |
| `excel-agent-tools/excel-tools.env` | слушатель `:8000`, сессии, лимиты книг | `EXCEL_TOOLS_HOST=0.0.0.0`; ключ не нужен |
| `schedule-builder-service/schedule-builder.env` | слушатель `:8090`, `ACTIVITY_BASE_URL` | то же |
| `fastapi-math-service/math-service.env` | слушатель `:8100` | то же |
| `agents-template/demo_agent/demo-agent.env` | шаблон агента `:8300` (не полевой контур) | lab / проверка шаблона |
| корневой `.env` | только lab Compose (Postgres, n8n, порты) | на поле не используется |

Проверка переменных Activity: каждая строка `Settings` есть в `mas-activity.env.example` (pytest `test_settings`).

### 4. Связать workflows

В каждом workflow нода **Execute Workflow** должна указывать на живой workflow, не на `REPLACE_…`.

| Workflow | Нода | Цель |
|---|---|---|
| Orchestrator — MAS | Runtime endpoints | MAS — Runtime Config |
| Оба агента | Runtime configuration | MAS — Runtime Config |
| Оркестратор и оба агента | Call Knowledge Retrieval | MAS — Knowledge Retrieval |
| Форма Health Check | Runtime endpoints | MAS — Runtime Config; на пробах webhook — тот же Header Auth |

Агентов к оркестратору кнопкой «привязать ноду» не цепляют. После импорта впишите новые id в `Runtime URLs` → `agent_workflow_ids`:

```json
{"excel_extractor":"<id из URL Excel>","schedule_builder":"<id из URL Schedule>"}
```

Save. Либо позже, когда Activity уже жива: страница **Агенты** → правите `invoke.workflow_id`.

Settings каждого из: оркестратор, оба агента, Retrieval, Ingestion → **Error workflow** = `Error — MAS Node Traces`. На сам Error traces и на прокси это не ставьте.

### 5. Прокси и таблицы

1. В **MAS — Control Plane Proxy** проверьте Header Auth и Postgres → **Activate**.
2. Вызовите `POST /webhook/mas-control-plane` с телом `{"operation":"schema"}` и тем же Header Auth. Ответ `ok: true`. Роли n8n нужны права `CREATE TABLE`.
3. Теперь запускайте Activity (`start-windows.bat` в `mas-activity-service`).
4. `check-all-windows.bat` — все четыре сервиса OK, Activity `/ready` = 200, в `/health` поле `control_plane_backend` = `n8n_proxy`.

Очистку кейсов (`clear`) сами не включайте.

### 6. База знаний

1. В **MAS — Knowledge Ingestion** те же Postgres и Embeddings, что у Retrieval. Активируйте webhook.
2. Activity → **База знаний** → **Загрузить в RAG**. Первый ingest после импорта пишет `tnavigator_schedule_knowledge_v2` (`baai/bge-m3`). В ответе должны быть ненулевые срезы для оркестратора и Excel.

### 7. Проверка и включение

1. В n8n откройте `/form/mas-deployment-health-check` (нужна ваша сессия). Цель — **PASS**, ни одного FAIL. Строка версии = `VERSION`.
2. Активируйте по очереди: Ingestion, Retrieval, Excel Extractor, Schedule Builder, Error traces, **последним** оркестратор.
3. Прогоните форму ещё раз.

---

## Как пользоваться

Откройте Activity: `http://<IP-Windows>:8200`.

Новая задача — текст цели и файлы. Лента обновляется сама. Когда статус «ждём ответ» — нажмите кнопку или напишите обычную фразу своими словами (система поймёт и сопоставит с вариантами). Файлы можно докинуть перетаскиванием. Результаты — панель справа и чипы под сообщениями. Вкладка **Схема** показывает, какие агенты уже отработали. **База знаний** — карточки и повторная загрузка в RAG.

Галочка **Режим разработчика** открывает вкладку **Лог**: шаги оркестратора, запрос в базу знаний и найденные карточки, токены и `finish_reason` каждого решения, вызовы инструментов, ошибки узлов n8n со ссылкой на execution. Эти строки в чат инженера не попадают. Для обычной работы вкладка не нужна.

## Что видно в логе

Лог — те же события кейса, что лента, плюс технические виды. Чат их скрывает. Открывается галочкой **Режим разработчика** → **Лог**, либо `GET /cases/{id}/log` (скачать: `?format=ndjson`).

| Вид | Что это | Как читать |
|---|---|---|
| `orchestrator.decision` | шаг оркестратора | действие, `guard`, токены, `finish_reason`; в payload всегда `llm` и `rag`. В логе **после** `trace.rag` / `trace.llm` того же шага |
| `trace.llm` | ход модели (Decision / Verify / Interpret / агент `role=agent`) | токены, `finish_reason`; `length` = ответ съеден thinking — гейт `llm_truncated`. Промпт — блоки `role` + текст, не одна JSON-строка |
| `trace.rag` | запрос в базу знаний | query, status, карточки (`knowledge_id`, score, ветки). `unavailable` / 0 карточек у Schedule Builder на задачах «даты ввода» пока ожидаемо |
| `trace.tool` | вызов инструмента FastAPI | имя, ok, длительность, аргументы; в α2 не входит `retrieve_knowledge` |
| `orchestrator.resume` | продолжение `source=agent` или `system` | ссылка на execution, если в payload есть `execution_id` и `workflow_id` |
| `system.node_error` | упал узел n8n | имя узла + ссылка на execution |

Сводка шапки лога: шаги, передачи, инструменты, **запросы в базу** (`kb_calls` — только `retrieve_knowledge`, `phase=on_demand`), **пустой RAG** (`rag_empty`), **усечения LLM** (`llm_truncated`), вопросы инженеру, предупреждения. Ссылка на execution появляется только при паре id в payload и заданном `N8N_PUBLIC_URL` (адрес n8n в браузере). В lab с консоли: `python3 scripts/mas_trace_case.py CASE-…`.

Если задача зависла в «идёт» без новых сообщений дольше пары минут — в ленте есть **Продолжить**. **Закрыть задачу** помечает её отменённой. **Перезапустить с теми же файлами** — если сервис агента не был поднят.

---

## Если не заводится

| Что видите | Что сделать |
|---|---|
| Activity не стартует или 404 на `mas-control-plane` | Сначала активируйте прокси, потом Activity. Проверьте Header Auth. |
| `/health` без `n8n_proxy` | Не задан `CONTROL_PLANE_PROXY_URL` — для работы так нельзя. |
| `relation "cases" does not exist` | Ещё раз `{"operation":"schema"}`. Проверьте Postgres и права CREATE. |
| «Агент недоступен» | Не прописаны id в `agent_workflow_ids` или агент выключен на странице «Агенты». Workflow агента должен быть Active. |
| Excel 401 | В `excel-tools.env` остался старый `API_KEY` — уберите его. Сервис слушает `:8000`. |
| n8n не видит сервисы | Firewall; в env `0.0.0.0`; в Runtime Config IP Windows, не docker-имя. |
| Health Check 404 / 403 на оркестратор | Оркестратор не Active, или `orchestrator_step_url` — не тот адрес, с которого n8n ходит сам в себя, или Header Auth не совпал. |
| Форма Health Check по адресу `/webhook/…` даёт 404 | Это форма: `/form/mas-deployment-health-check`. |
| «Загрузить в RAG» → 404 | Ingestion не Active. |
| Пустая лента при живом n8n | Переимпортировали прокси — перезапустите Activity. |
| «Сервис агента не отвечает», задача сразу упала | Не запущен Excel/Schedule или неверный URL в Runtime Config. Поднимите сервис и перезапустите задачу. То же, если сервис упал **в середине** цикла инструментов. |
| «Модель чата не ответила», задача сразу упала | Нет ответа `/chat/completions` (сеть, 5xx, пустой choices). Проверьте credential, `chat_base_url` и что корп. vLLM умеет function calling. Не путать с «агент не вернул факты». |
| «Не удалось разобрать следующий шаг» | Модель вернула пустой ответ. В Логе смотрите строку решения. Три раза подряд задача падает. Проверьте, что на Decision chat, Verify chat и Interpret chat висит тот же credential и `chat_base_url` совпадает с Base URL модели. |
| Вопрос с кнопками, вы ответили текстом, система переспросила «не поняла» | Напишите ближе к подписи кнопки или нажмите кнопку. Interpret chat должен быть на том же credential, что Decision. |
| Activity пишет, что не дождалась шага, задача остаётся «идёт» | n8n ещё считает (лимит ожидания 30 мин). Это не «неверный адрес». Если connection refused — тогда да, адрес оркестратора. |
| В логе нет ссылок на execution | Задайте `N8N_PUBLIC_URL` — адрес n8n, как вы его открываете в браузере. |
