"""tNav Cluster Agent: контракт агента и доменная логика на моке ГД-кластера.

Мок — это настоящий каталог с моделями, настоящие ``cp``/``grep``/``find`` в песочнице и заглушка
``tNavigator-con``, которая ведёт себя как консольный расчёт (``RESULTS/<имя>.log|err|end|lock``).
Поэтому здесь проверяется тот же код, что поедет в поле: меняется только транспорт (local вместо ssh).

Кейсы из плана: запуск расчёта новой версии модели на кластере (Фаза 10), запрет удаления файлов,
один корень работы, долгий расчёт с колбеком (``in_progress`` → ``finish_task``).
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent import TnavClusterAgent
from app.cluster import ClusterWorkspace, _state_of
from app.model_files import count_dates, parse_deck
from app.settings import ClusterSettings
from app.shell import ClusterError, CommandNotAllowed, LocalShell, PathOutsideRoot
from app.simulation import LogDigest, ResultsLayout, RunHandle, SimulatorProfile, duration_ru, estimate
from mas_agent_kit import create_agent_app, human_text_problems
from mock_cluster import build_model_tree

MOCK_CON = Path(__file__).resolve().parents[1] / "mock_cluster" / "tnav_con_mock.py"
NEW_SCHEDULE = """-- прогноз из Schedule Builder
DATES
  1 FEB 2027 /
/

WCONPROD
  P01 OPEN LRAT 3* 300 1* 120 /
/

DATES
  1 MAR 2027 /
/
"""


# -- окружение теста --------------------------------------------------------------------------------


class _FakeActivity(BaseHTTPRequestHandler):
    """Activity для агента: отдаёт state кейса и файл расписания, копит всё, что агент прислал."""

    calls: list[dict[str, Any]] = []
    artifact: bytes = NEW_SCHEDULE.encode("utf-8")
    inline: bool = False

    def log_message(self, *_: Any) -> None:
        return

    def _send(self, payload: dict[str, Any] | bytes, *, content_type: str = "application/json", disposition: str = "") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if "/artifacts/" in self.path:
            self._send(self.artifact, content_type="text/plain", disposition='attachment; filename="FORECAST_2027.INC"')
            return
        card: Any = (
            NEW_SCHEDULE
            if _FakeActivity.inline
            else {"artifact_id": "schedule_out", "role": "schedule_out", "filename": "FORECAST_2027.INC", "bytes": len(self.artifact), "kind": "deliverable"}
        )
        self._send({"state": {"artifacts": {"schedule_out": card}, "agents": {}}})

    def do_POST(self) -> None:  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {"raw_bytes": len(raw)}
        _FakeActivity.calls.append({"path": self.path, "json": body})
        self._send({"ok": True})


@pytest.fixture
def activity_url():
    _FakeActivity.calls = []
    _FakeActivity.inline = False
    _FakeActivity.artifact = NEW_SCHEDULE.encode("utf-8")
    server = HTTPServer(("127.0.0.1", 0), _FakeActivity)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


@pytest.fixture
def cluster(tmp_path) -> dict[str, str]:
    return build_model_tree(tmp_path / "cluster", forecast_dates=6, history_dates=2)


@pytest.fixture
def settings(tmp_path, cluster, monkeypatch) -> ClusterSettings:
    monkeypatch.setenv("TNAV_MOCK_SECONDS", "0.3")
    return ClusterSettings(
        transport="local",
        root=str(tmp_path / "cluster"),
        cli_command=f"{sys.executable} {MOCK_CON} --cpu-num=2 --log-lang=ru --dump-res {{model}}",
        poll_seconds=0.05,
        progress_every_s=0.05,
        command_timeout_s=60.0,
    )


@pytest.fixture
def workspace(settings) -> ClusterWorkspace:
    return ClusterWorkspace(settings)


@pytest.fixture
def agent(settings, tmp_path) -> TnavClusterAgent:
    """Тот же агент, что поднимает сервис (инструменты зарегистрированы на нём), но на моке кластера."""
    from app.agent import agent as service_agent

    return service_agent.bind(settings, sessions_root=str(tmp_path / "sessions"), poll_seconds=0.05, progress_every_s=0.05)


@pytest.fixture
def client(agent) -> TestClient:
    from app.main import app as service_app

    return TestClient(service_app)


def task(activity_url: str, objective: str, case_id: str = "CASE-cluster") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "task_id": "T-1",
        "agent_id": "tnav_cluster",
        "objective": objective,
        "inputs": {"activity_base_url": activity_url},
        "context": {},
    }


def wait_for_run(agent: TnavClusterAgent, session_id: str, timeout: float = 60.0) -> None:
    thread = agent.jobs[session_id]
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "наблюдатель расчёта не завершился"


# -- настройки ---------------------------------------------------------------------------------------


def test_settings_read_the_field_variables() -> None:
    parsed = ClusterSettings.from_env(
        {
            "TNAV_SSH_HOST": "cluster.local",
            "TNAV_SSH_USER": "re",
            "TNAV_SSH_PASSWORD": "secret",
            "TNAV_CLUSTER_ROOT": "/data/models/",
            "TNAV_CLI_COMMAND": "/opt/tNavigator/tNavigator-con --cpu-num=16 {model}",
            "TNAV_POLL_SECONDS": "30",
        }
    )
    assert parsed.transport == "ssh" and parsed.ready and parsed.root == "/data/models"
    assert parsed.simulator_program() == "/opt/tNavigator/tNavigator-con"
    assert parsed.simulator_options() == ["--cpu-num=16"]
    assert parsed.poll_seconds == 30
    assert parsed.listen_host == "127.0.0.1" and parsed.listen_port == 8400
    assert "secret" not in json.dumps(parsed.secret_free()), "пароль не попадает в лог разработчика"


def test_settings_keep_password_equals_and_listen_bind() -> None:
    """CMD ``for /f tokens=1,* delims==`` would split ``p@ss=word``; Python dotenv must not."""
    parsed = ClusterSettings.from_env(
        {
            "TNAV_SSH_HOST": "cluster.local",
            "TNAV_SSH_USER": "re",
            "TNAV_SSH_PASSWORD": "p@ss=word",
            "TNAV_CLUSTER_HOST": "0.0.0.0",
            "TNAV_CLUSTER_PORT": "8400",
            "TNAV_CLUSTER_ROOT": "/data/models",
            "TNAV_CLI_COMMAND": "/opt/tNavigator/tNavigator-con --cpu-num=8 {model}",
        }
    )
    assert parsed.ssh.password is not None
    assert parsed.ssh.password.get_secret_value() == "p@ss=word"
    assert parsed.listen_host == "0.0.0.0" and parsed.listen_port == 8400


def test_service_starts_without_credentials_and_says_what_is_missing() -> None:
    bare = ClusterSettings.from_env({})
    assert bare.ready is False
    assert bare.problems and all(human_text_problems(line, min_length=8) == [] for line in bare.problems)


def test_cli_command_needs_the_model_placeholder() -> None:
    with pytest.raises(ValueError, match="model"):
        ClusterSettings(cli_command="/opt/tNavigator/tNavigator-con --cpu-num=8")
    with pytest.raises(ValueError, match="unix"):
        ClusterSettings(root="data/models")


def test_simulator_profile_quotes_the_model_path() -> None:
    profile = SimulatorProfile(command="/opt/tNav/tNavigator-con --cpu-num=8 {model}")
    script = profile.launch_script("/data/models/M 1/M.data", console_path="/data/models/M 1/RESULTS/M.console.log", workdir="/data/models/M 1")
    assert "'/data/models/M 1/M.data'" in script and script.endswith("echo $!")
    assert "nohup" in script, "расчёт живёт дольше SSH-сессии"


# -- безопасность оболочки ----------------------------------------------------------------------------


def test_deleting_and_escaping_never_reach_the_cluster(settings) -> None:
    shell = LocalShell(settings)
    with pytest.raises(CommandNotAllowed, match="rm"):
        shell.run(["rm", "-rf", shell.path("MODELS")])
    for program in ("chmod", "dd", "kill"):
        with pytest.raises(CommandNotAllowed):
            shell.run([program, "x"])
    with pytest.raises(CommandNotAllowed, match="curl|разрешённых"):
        shell.run(["curl", "http://example.com"])
    with pytest.raises(PathOutsideRoot):
        shell.path("../../etc/passwd")
    with pytest.raises(PathOutsideRoot):
        shell.path("/etc/passwd")
    assert shell.path("MODELS/../MODELS/SEVER") == f"{settings.root}/MODELS/SEVER"


def test_shell_reads_counts_and_copies(settings, cluster) -> None:
    with LocalShell(settings) as shell:
        assert shell.is_file(cluster["sever"]) and shell.is_dir("MODELS/SEVER")
        assert "RUNSPEC" in shell.read_text(cluster["sever"], limit=4000)
        assert shell.count_lines(cluster["sever_schedule"], r"^[[:space:]]*DATES([[:space:]]|$)") == 6
        shell.copy(cluster["sever"], "MODELS/SEVER/SEVER_copy.data")
        assert shell.is_file("MODELS/SEVER/SEVER_copy.data")
        assert sorted(shell.find_files("*.data", limit=10)) == [
            "MODELS/SEVER/SEVER.data",
            "MODELS/SEVER/SEVER_copy.data",
            "MODELS/YUG/DATA/YUG.data",
        ]


# -- разбор модели -------------------------------------------------------------------------------------


def test_deck_parsing_finds_sections_and_includes(cluster, tmp_path) -> None:
    outline = parse_deck((tmp_path / "cluster" / cluster["sever"]).read_text(encoding="utf-8"))
    assert outline.section_names == ["RUNSPEC", "GRID", "PROPS", "SOLUTION", "SUMMARY", "SCHEDULE"]
    schedule_includes = [ref.path for ref in outline.includes if ref.section == "SCHEDULE"]
    assert schedule_includes == ["INCLUDE/SCHEDULE/HISTORY.INC", "INCLUDE/SCHEDULE/FORECAST.INC"]


def test_layout_picks_the_schedule_with_the_most_dates(workspace, cluster) -> None:
    layout = workspace.layout(cluster["sever"])
    assert layout.main_schedule is not None
    assert layout.main_schedule.path == cluster["sever_schedule"], "основной файл расписания — с наибольшим числом дат"
    assert layout.main_schedule.dates == 6
    assert [item.dates for item in layout.schedule_files] == [6, 2], "кандидаты отсортированы по числу дат"
    assert layout.missing_includes == []
    assert layout.results.log.endswith("RESULTS/SEVER.log")


def test_layout_resolves_a_root_relative_include(workspace, cluster) -> None:
    layout = workspace.layout(cluster["yug"])
    assert layout.main_schedule is not None and layout.main_schedule.path == cluster["yug_schedule"]
    assert layout.missing_includes == []


def test_layout_of_an_unknown_model_is_an_error(workspace) -> None:
    with pytest.raises(ClusterError, match="не найден"):
        workspace.layout("MODELS/NOPE/NOPE.data")


# -- новая версия модели --------------------------------------------------------------------------------


def test_create_version_copies_the_deck_and_repoints_the_include(workspace, cluster, tmp_path) -> None:
    layout = workspace.layout(cluster["sever"])
    version = workspace.create_version(layout, "prognoz 2027!", NEW_SCHEDULE.encode("utf-8"))
    root = tmp_path / "cluster"
    assert version.model_path == "MODELS/SEVER/SEVER_prognoz_2027.data"
    assert version.schedule_path == "MODELS/SEVER/INCLUDE/SCHEDULE/FORECAST_prognoz_2027.INC"
    assert version.dates_total == 2
    new_deck = (root / version.model_path).read_text(encoding="utf-8")
    assert "INCLUDE/SCHEDULE/FORECAST_prognoz_2027.INC" in new_deck
    assert "INCLUDE/SCHEDULE/FORECAST.INC'" not in new_deck, "старый путь расписания в копии не остался"
    assert "INCLUDE/SCHEDULE/HISTORY.INC" in new_deck, "остальные подключения не тронуты"
    # Исходная модель и исходное расписание на месте и не изменились.
    assert (root / cluster["sever"]).read_text(encoding="utf-8").count("FORECAST.INC") == 1
    assert count_dates((root / cluster["sever_schedule"]).read_text(encoding="utf-8")) == 6
    assert (root / version.schedule_path).read_text(encoding="utf-8") == NEW_SCHEDULE


def test_create_version_refuses_to_overwrite(workspace, cluster) -> None:
    layout = workspace.layout(cluster["sever"])
    workspace.create_version(layout, "v2", NEW_SCHEDULE.encode("utf-8"))
    with pytest.raises(ClusterError, match="уже существует"):
        workspace.create_version(layout, "v2", NEW_SCHEDULE.encode("utf-8"))


# -- расчёт ----------------------------------------------------------------------------------------------


def test_console_banner_is_not_failure_while_lock_is_present() -> None:
    """CASE-6aad780d-952112: nohup console (banner + «Команда:») is not .err while .lock exists."""
    digest = LogDigest(finished=False, steps=1, current_date="1 FEB 2027")
    banner = "tNavigator 24.4 (mock console build)\nКоманда: python3 tnav_con_mock.py --dump-res MODEL"
    assert _state_of(alive=False, lock=True, digest=digest, end_report=None, error_tail=banner) == "running"
    assert _state_of(alive=False, lock=False, digest=digest, end_report=None, error_tail=banner) == "failed"


def test_run_finishes_and_reports_the_facts(workspace, cluster) -> None:
    layout = workspace.layout(cluster["sever"])
    handle = workspace.launch(layout.data_path, dates_total=layout.dates_total)
    assert handle.pid > 0 and handle.results.results_dir.endswith("RESULTS")
    status = _poll(workspace, handle)
    assert status.state == "finished"
    # Симулятор считает всю секцию расписания: 2 даты истории + 6 дат прогноза.
    assert status.digest.steps == 8
    assert status.digest.current_date and status.digest.elapsed_s >= 0
    assert status.end_report is not None and status.end_report.errors == 0
    assert human_text_problems(status.human_progress()) == []


def test_failed_run_is_reported_with_the_error_log(workspace, cluster, monkeypatch) -> None:
    monkeypatch.setenv("TNAV_MOCK_FAIL", "1")
    handle = workspace.launch(cluster["sever"], dates_total=6)
    status = _poll(workspace, handle)
    assert status.state == "failed"
    assert "WELSPECS" in status.error_tail or "P09" in status.error_tail
    assert human_text_problems(status.human_progress()) == []


def test_a_running_model_is_not_launched_twice(workspace, cluster, monkeypatch) -> None:
    import time

    monkeypatch.setenv("TNAV_MOCK_SECONDS", "3")
    handle = workspace.launch(cluster["sever"], dates_total=6)
    deadline = time.time() + 10
    with workspace.shell() as shell:
        while not shell.exists(handle.results.lock) and time.time() < deadline:
            time.sleep(0.05)
        assert shell.exists(handle.results.lock), "симулятор не поставил файл блокировки"
    with pytest.raises(ClusterError, match="блокировки"):
        workspace.launch(cluster["sever"], dates_total=6)
    _poll(workspace, handle)  # дождаться конца, чтобы песочница удалилась без гонки


def _poll(workspace: ClusterWorkspace, handle: RunHandle, timeout: float = 60.0):
    import time

    deadline = time.time() + timeout
    status = workspace.status(handle)
    while not status.done and time.time() < deadline:
        time.sleep(0.1)
        status = workspace.status(handle)
    assert status.done, f"расчёт не завершился: {status.state}"
    return status


# -- разбор лога -------------------------------------------------------------------------------------------


def test_log_digest_reads_the_manual_shaped_log() -> None:
    digest = LogDigest.parse(
        "\n".join(
            [
                "tNavigator 24.4",
                "Чтение модели: SEVER.data",
                "Шаг 1: 1 JUL 2019, dt=31 сут, Newton #=2, |F|=8.7e+003, its=18. (1)",
                "Шаг 2: 1 AUG 2019, dt=31 сут, Newton #=3, |F|=5.1e+003, its=21. (2)",
                "WARNING: скважина P02 закрыта по экономике",
                "Статистика загрузки: 56608,12235,16403",
                "Время расчёта=01.50.30, CPU=01.15.15, шагов=550, NI=819, LI=18678",
            ]
        )
    )
    assert digest.finished and digest.steps == 550 and digest.warnings == 1
    assert digest.elapsed_s == 6630 and digest.cpu_s == 4515
    assert digest.newton_iterations == 819 and digest.linear_iterations == 18678
    assert digest.current_date == "1 AUG 2019"
    assert digest.errors == []


def test_eta_comes_from_the_schedule_dates() -> None:
    handle = RunHandle(model_path="M.data", model_name="M", results=ResultsLayout.for_model("M.data"), dates_total=100)
    fraction, eta = estimate(handle, LogDigest(steps=25), elapsed_s=600)
    assert fraction == 0.25 and eta == 1800
    assert estimate(handle, LogDigest(steps=0), elapsed_s=600) == (None, None)
    assert duration_ru(1800) == "30 минут" and duration_ru(6630) == "1 час 50 минут"


# -- контракт агента ---------------------------------------------------------------------------------------


def test_health_lists_tools_and_the_cluster_config(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True and body["agent_id"] == "tnav_cluster"
    assert body["tools"] == ["list_models", "inspect_model", "prepare_model_version", "start_calculation", "check_calculation", "ask_engineer"]
    assert body["cluster_ready"] is True and body["cluster"]["root"].endswith("cluster")
    assert body["cluster_problems"] == []
    assert "env_files" in body


def test_full_flow_prepares_a_version_and_runs_it(client: TestClient, agent: TnavClusterAgent, activity_url: str, cluster) -> None:
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Собери новую версию модели с новым расписанием и посчитай её на кластере")).json()
    assert opened["ok"] is True
    sid = opened["session_id"]
    assert [item["model"] for item in opened["inspect"]["models"]] == [cluster["sever"], cluster["yug"]]
    assert opened["inspect"]["schedule_files"][0]["artifact_id"] == "schedule_out"
    assert opened["inspect"]["simulator"]["program"].endswith("python3") or "python" in opened["inspect"]["simulator"]["program"]

    looked = client.post("/agent-tools/inspect_model", json={"session_id": sid, "model": cluster["sever"]}).json()
    assert looked["main_schedule"] == {"file": cluster["sever_schedule"], "dates": 6}
    assert looked["sections"][0] == "RUNSPEC" and "next_step" in looked

    prepared = client.post("/agent-tools/prepare_model_version", json={"session_id": sid, "version_name": "prognoz_2027"}).json()
    assert prepared["status"] == "prepared"
    assert prepared["model"] == "MODELS/SEVER/SEVER_prognoz_2027.data"
    assert prepared["schedule"].endswith("FORECAST_2027.INC"), "имя файла расписания берётся из карточки задачи"
    assert prepared["dates"] == 2

    started = client.post("/agent-tools/start_calculation", json={"session_id": sid}).json()
    assert started["status"] == "in_progress" and started["model"] == prepared["model"]
    pending = client.get(f"/sessions/{sid}/result").json()
    assert pending["status"] == "in_progress" and pending["watch"]["kind"] == "cluster_run"
    assert human_text_problems(pending["message"]) == []
    # CASE-6aad7bd4-871c26: Qwen kept calling check_calculation; the mock finished first and
    # finish_task raced the agent loop — never waiting_agent, two agent.result, tool_calls=7.
    blocked = client.post("/agent-tools/check_calculation", json={"session_id": sid}).json()
    assert blocked["ok"] is False and blocked["code"] == "result_already_stored"
    assert "error" not in blocked

    wait_for_run(agent, sid)
    resume = [call for call in _FakeActivity.calls if call["path"].endswith("/run")]
    assert len(resume) == 1 and resume[0]["json"]["source"] == "agent"
    final = resume[0]["json"]["agent_result"]
    assert final["status"] == "completed" and final["data"]["run_status"] == "finished"
    # 2 даты истории + 2 даты нового прогноза: считается именно новая версия, а не исходная (там 6).
    assert final["data"]["run_results"]["steps"] == 4
    assert final["data"]["model_version"]["model_path"] == prepared["model"]
    assert "расчётных шагов" in final["message"] and human_text_problems(final["message"]) == []
    progress = [call["json"] for call in _FakeActivity.calls if call["path"].endswith("/events")]
    assert all(human_text_problems(event["status_message"]) == [] for event in progress if event["kind"] == "agent.progress")
    assert any(call["path"].endswith("/artifacts") for call in _FakeActivity.calls), "журнал расчёта приложен к задаче"


def test_prepare_uses_the_inline_schedule_text(client: TestClient, activity_url: str, cluster) -> None:
    _FakeActivity.inline = True
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Подготовь версию модели")).json()
    sid = opened["session_id"]
    client.post("/agent-tools/inspect_model", json={"session_id": sid, "model": cluster["sever"]})
    prepared = client.post("/agent-tools/prepare_model_version", json={"session_id": sid}).json()
    assert prepared["status"] == "prepared" and prepared["dates"] == 2
    # Расчёт не запускали: итог сессии — собранная версия, а не «непонятно, что делать».
    result = client.get(f"/sessions/{sid}/result").json()
    assert result["status"] == "completed" and result["data"]["run_status"] == "not_started"
    assert human_text_problems(result["message"]) == []


def test_a_file_without_dates_is_an_llm_error(client: TestClient, activity_url: str, cluster) -> None:
    _FakeActivity.artifact = b"-- pusto\nWEFAC\n P01 0.9 /\n/\n"
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Подготовь версию модели")).json()
    sid = opened["session_id"]
    client.post("/agent-tools/inspect_model", json={"session_id": sid, "model": cluster["sever"]})
    bad = client.post("/agent-tools/prepare_model_version", json={"session_id": sid}).json()
    assert bad["ok"] is False and bad["code"] == "schedule_file_without_dates"
    assert "error" not in bad, "ключ error в ответе инструмента n8n считает падением ноды"


def test_starting_without_a_model_is_an_llm_error(client: TestClient, activity_url: str) -> None:
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Посчитай модель")).json()
    bad = client.post("/agent-tools/start_calculation", json={"session_id": opened["session_id"]}).json()
    assert bad["ok"] is False and bad["code"] == "model_not_chosen"
    assert bad["available_models"], "модели подсказаны модели, а не инженеру"


def test_ask_engineer_stores_needs_input_in_russian(client: TestClient, activity_url: str) -> None:
    opened = client.post("/agent-tools/open_session", json=task(activity_url, "Посчитай модель на кластере")).json()
    sid = opened["session_id"]
    bad = client.post("/agent-tools/ask_engineer", json={"session_id": sid, "question": "Уточните model_path для data_file"}).json()
    assert bad["ok"] is False and bad["code"] == "question_not_human"
    ok = client.post(
        "/agent-tools/ask_engineer",
        json={"session_id": sid, "question": "Какую модель считать на кластере?", "options": "Северная; Южная"},
    ).json()
    assert ok["status"] == "needs_input"
    result = client.get(f"/sessions/{sid}/result").json()
    assert result["status"] == "needs_input" and [o["label"] for o in result["requests"][0]["options"]] == ["Северная", "Южная"]
    assert human_text_problems(result["message"]) == []


def test_unreachable_cluster_is_a_failed_result_not_a_question(activity_url: str, tmp_path) -> None:
    broken = TnavClusterAgent(
        root=str(tmp_path / "sessions2"),
        settings=ClusterSettings(transport="local", root=str(tmp_path / "missing"), cli_command="/bin/true {model}"),
    )
    client = TestClient(create_agent_app(broken, title="tnav-cluster"))
    body = client.post("/agent-tools/open_session", json=task(activity_url, "Посчитай модель")).json()
    assert body["ok"] is False and body["status"] == "failed"
    assert body["result"]["issues"][0]["type"] == "cluster_unreachable"
    assert human_text_problems(body["result"]["message"]) == []
