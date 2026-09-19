# tNav Cluster Agent — модели и расчёты на гидродинамическом кластере

Агент MAS, который работает с моделью tNavigator **на кластере по SSH**: находит входной `.data` файл,
понимает его секции (`RUNSPEC`, `GRID`, `PROPS`, … `SCHEDULE`), определяет **основной файл расписания**
(тот из `INCLUDE`, где больше всего `DATES`), кладёт рядом новое расписание от Schedule Builder, делает
**копию** входного файла модели с новым `INCLUDE`, запускает расчёт командой `tNavigator-con` и следит за
ним до конца — от минут до суток — после чего сам возвращает оркестратору время и итог расчёта.

Ничего не удаляет: `rm` и родня запрещены на уровне кода (`app/shell.py`, `FORBIDDEN_PROGRAMS`).
Все пути — только внутри одного каталога из настроек (`TNAV_CLUSTER_ROOT`), выход в `..` невозможен.

```
tnav-cluster-service/
├── app/__init__.py       # kit на sys.path + load_service_env(tnav-cluster.env), не CMD for /f
├── app/__main__.py       # python -m app: host/port из ClusterSettings
├── app/settings.py       # ClusterSettings (pydantic v2): SSH, корень, команда расчёта, таймауты
├── app/shell.py          # ClusterShell → SshShell (paramiko exec+SFTP) / LocalShell; белый список команд
├── app/model_files.py    # разбор .data: секции, INCLUDE, счёт DATES
├── app/simulation.py     # профиль симулятора, чтение .log/.err/.end, оценка остатка
├── app/cluster.py        # ClusterWorkspace: найти модели, разобрать, собрать версию, запустить, статус
├── app/agent.py          # TnavClusterAgent(AgentService) + фоновое наблюдение за расчётом
├── app/agent_tools.py    # шесть инструментов для LLM
├── app/main.py           # app = create_agent_app(agent, extra_health=…)
├── mock_cluster/         # мок ГД-кластера: дерево моделей, фейковый tNavigator-con, SSH-сервер
├── tests/                # pytest: логика + полный путь по SSH против мока
└── tnav-cluster.env.example
```

Вторая половина агента — спека `n8n/templates/agents/tnav_cluster.py` (`AgentSpec`): из неё генерируются
workflow `n8n/workflows/core/tnav-cluster-agent.workflow.json`, поле `tnav_cluster_url` в `MAS — Runtime Config`
и строка `agent_registry`. Оркестратор при этом не меняется.

---

## 1. Куда вписывать адрес кластера, логин, пароль и пути

**Один файл: `tnav-cluster.env` рядом с сервисом** (создаётся копией `tnav-cluster.env.example`).
Больше нигде пароль не нужен: ни в workflow n8n, ни в коде, ни в базе.

```bat
setup-windows.bat
copy tnav-cluster.env.example tnav-cluster.env
notepad tnav-cluster.env
start-windows.bat
```

`start-windows.bat` не парсит файл: Python читает `tnav-cluster.env` через kit `load_service_env` (`utf-8-sig`), как Activity. CMD `for /f` на поле ломал BOM и пароли с `=`.

| Переменная | Что вписать | Пример |
|---|---|---|
| `TNAV_SSH_HOST` | адрес ГД-кластера (имя или IP), как он виден с этой Windows | `cluster.example.local` |
| `TNAV_SSH_PORT` | порт SSH, если не 22 | `22` |
| `TNAV_SSH_USER` | ваша учётка на кластере | `re_engineer` |
| `TNAV_SSH_PASSWORD` | пароль этой учётки | `…` |
| `TNAV_SSH_KEY_PATH` | **вместо** пароля — путь к приватному ключу на этой Windows | `C:\Users\me\.ssh\id_ed25519` |
| `TNAV_SSH_KEY_PASSPHRASE` | парольная фраза ключа, если он с фразой | `…` |
| `TNAV_CLUSTER_ROOT` | **единственный** каталог на кластере, в котором агенту разрешено работать | `/data/models` |
| `TNAV_CLI_COMMAND` | команда расчёта: путь к `tNavigator-con` + ваши опции + `{model}` | см. ниже |
| `TNAV_RESULTS_DIRNAME` | имя каталога результатов рядом с моделью | `RESULTS` |

Пароль или ключ — что-то одно должно быть заполнено. Пока не заполнено, сервис всё равно запускается и
в `GET /health` пишет, чего именно не хватает (`cluster_problems`), а задача завершается понятным русским
текстом, а не вопросом инженеру.

**Путь `TNAV_CLUSTER_ROOT` — граница работы.** Все пути моделей агент принимает и печатает **относительно
него** (`SEVER/SEVER.data`, а не `/data/models/SEVER/SEVER.data`). `..`, абсолютные пути и симлинки за
пределы каталога отклоняются (`PathOutsideRoot`). Если модели лежат в нескольких местах — задайте общий
каталог выше (`/data`) и увеличьте `TNAV_SCAN_DEPTH`.

### Команда расчёта: где менять путь к tNavigator и опции

Строка целиком ваша, агент подставляет в неё только путь к модели вместо `{model}`:

```
TNAV_CLI_COMMAND=/opt/tNavigator/tNavigator-con --cpu-num=8 --log-lang=ru --dump-res {model}
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^
    путь к исполняемому файлу          ваши опции (мануал tNavigator, 8.1)   подставит агент
```

- `{model}` обязателен — без него сервис не стартует (валидатор `ClusterSettings.cli_command`).
- Опции ни на что в коде не влияют: агент их не разбирает, а передаёт кластеру как есть. Полезные из
  мануала: `--cpu-num`, `--mpi-num`, `--log-lang=ru`, `--dump-res`, `--max-calc-time`.
- Значение по умолчанию (если переменная не задана) лежит в коде одной строкой:
  `app/settings.py` → `DEFAULT_CLI_COMMAND`. Правьте env, а не код.
- Запуск идёт через `nohup … &` из каталога модели, `.log` / `.err` / `.end` агент читает в
  `<каталог модели>/<TNAV_RESULTS_DIRNAME>/`. Если на вашем кластере имя другое — поменяйте
  `TNAV_RESULTS_DIRNAME`.

### Параметры наблюдения за расчётом

| Переменная | Смысл | По умолчанию |
|---|---|---|
| `TNAV_POLL_SECONDS` | как часто спрашивать кластер о состоянии расчёта | `60` |
| `TNAV_PROGRESS_EVERY_S` | как часто писать строку в чат инженеру | `900` (раз в 15 минут) |
| `TNAV_MAX_WAIT_HOURS` | предохранитель: дольше агент не ждёт (расчёт на кластере продолжается) | `72` |
| `TNAV_COMMAND_TIMEOUT_S` | таймаут короткой команды (`ls`, `grep`, `cp`) | `120` |
| `TNAV_SCAN_DEPTH` | глубина поиска `.data` под корнем | `4` |
| `TNAV_MAX_MODELS` | сколько моделей максимум показывать в инвентаре | `40` |
| `TNAV_LOG_TAIL_BYTES` | сколько байт хвоста лога читать за опрос | `20000` |

### Порт сервиса и связь с MAS

| Переменная | Что вписать |
|---|---|
| `TNAV_CLUSTER_HOST` | `0.0.0.0`, если до сервиса ходит корпоративный n8n (и откройте порт в firewall); иначе `127.0.0.1` |
| `TNAV_CLUSTER_PORT` | `8400` |
| `ACTIVITY_BASE_URL` | Activity на этой же машине, `http://127.0.0.1:8200` (резерв: обычно адрес приходит в задаче) |
| `TNAV_TRANSPORT` | `ssh` в поле; `local` — песочница на этой машине без SSH (тесты, демонстрация) |

В n8n адрес **этого** сервиса вписывается один раз: `MAS — Runtime Config` → нода `Runtime URLs` →
`tnav_cluster_url` = `http://<IP-этой-Windows>:8400`. Порядок полевых шагов — `docs.md`, раздел
«Кластерный агент».

---

## 2. Что агент умеет (инструменты LLM)

| Инструмент | Что делает |
|---|---|
| `list_models` | входные `.data` файлы под рабочим каталогом кластера |
| `inspect_model` | секции входного файла, подключённые `INCLUDE`, основной файл расписания (максимум `DATES`) |
| `prepare_model_version` | новое расписание рядом со старым + копия `.data` с переписанным `INCLUDE` |
| `start_calculation` | запуск расчёта; сразу отдаёт «в работе», итог придёт позже |
| `check_calculation` | состояние сейчас: идёт / завершился / упал, шаги, оценка остатка |
| `ask_engineer` | один вопрос инженеру по-русски, когда непонятно, какую модель считать |

Долгий расчёт идёт по контракту kit'а: `start_calculation` → `in_progress` + `watch` → кейс в
`waiting_agent` → фоновый поток пишет `agent.progress` и в конце `activity.finish_task(результат)`
(`POST /cases/{id}/run source=agent`). Инженер видит в чате «расчёт идёт, прошло N», затем итог с
временем расчёта и числом шагов; сводка расчёта уходит в «Результаты» как `out_run_summary`.

Данные для оркестратора (`agent_result.data`): `model_version` (путь копии, путь расписания, число дат),
`run_status` (`finished` / `failed` / `timeout` / `not_started`), `run_results` (каталог результатов, шаги,
секунды, последняя дата, хвост лога при падении).

---

## 3. Тесты и мок кластера

```bash
cd tnav-cluster-service
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python -m pytest -q          # логика + агент через HTTP
```

`tests/test_tnav_cluster.py` гоняет агента против **локальной песочницы** (`TNAV_TRANSPORT=local`) с
фейковым симулятором `mock_cluster/tnav_con_mock.py`: он ведёт себя как `tNavigator-con` из мануала —
пишет `RESULTS/<модель>.log` со строками шагов и датами, `.err` при ошибке, `.end` с кодом возврата,
`.lock` на время расчёта.

`tests/test_ssh_transport.py` поднимает **SSH-сервер** (`mock_cluster/ssh_server.py`, paramiko) над тем же
деревом моделей и проверяет полевой путь целиком: exec-команды, загрузку расписания по SFTP, запуск и
наблюдение за расчётом. Без установленного `paramiko` этот файл пропускается.

Дерево моделей для тестов и лаборатории строит `mock_cluster/model_tree.py` (две модели, `INCLUDE`,
история и прогноз). В lab Compose мок и сервис живут в одном контейнере `tnav-cluster`, сервис ходит к
моку по SSH — тем же транспортом, что в поле (`mock_cluster/lab_cluster.py`).

Живой кейс в системе: `python3 scripts/mas_gate.py --live --cases tnav_cluster`
(`simulation-model-example/run_live_tnav_cluster.py` — включает строку реестра, гонит задачу «примени
расписание и посчитай», проверяет паркинг и итог, выключает строку).

---

## 4. Как расширять

- **Новая опция настройки** — поле в `ClusterSettings` + строка в `from_env` + строка в
  `tnav-cluster.env.example` и в таблице выше.
- **Новый инструмент** — функция в `app/agent_tools.py` под `@agent.tools.tool(...)`, ошибки аргументов
  через `raise ToolError(code, подсказка модели, …)`; имя инструмента добавить в `TOOLS` спеки
  `n8n/templates/agents/tnav_cluster.py` и регенерировать workflow
  (`cd n8n/templates && python3 generate_tnav_cluster_agent.py`).
- **Новая команда на кластере** — имя программы в `ALLOWED_PROGRAMS` (`app/shell.py`). Удаляющие команды
  в этот список не добавляются: они перечислены в `FORBIDDEN_PROGRAMS` и отклоняются раньше запуска.
- **Другой симулятор или другая раскладка результатов** — `app/simulation.py`: `SimulatorProfile` (как
  строится команда) и `LogDigest` (как читается лог).
