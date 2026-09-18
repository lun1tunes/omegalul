"""tNav Cluster Agent — агент ГД-кластера на ``mas-agent-kit``: класс сервиса и долгий расчёт.

Что делает агент:

* ``list_models`` / ``inspect_model`` — находит модели под корнем кластера и разбирает ``.data`` файл:
  секции, подключённые файлы, основной файл schedule (тот, где больше всего строк ``DATES``);
* ``prepare_model_version`` — кладёт новый файл schedule (от Schedule Builder) рядом со старым, делает
  копию ``.data`` файла и переписывает в копии путь в ``INCLUDE``; ничего не удаляет;
* ``start_calculation`` — запускает расчёт командой из настроек, сразу отдаёт ``in_progress`` и следит за
  расчётом в фоне: строки прогресса в ленту, в конце — ``agent_result`` оркестратору через Activity;
* ``check_calculation`` — состояние расчёта прямо сейчас;
* ``ask_engineer`` — один вопрос инженеру по-русски, когда из задачи не ясно, какую модель считать.

Инструменты живут в ``app/agent_tools.py`` (импорт внизу файла), доменная логика — в ``app/cluster.py``.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from mas_agent_kit import (
    ActivityClient,
    AgentService,
    SessionNotFound,
    SessionStore,
    ToolRegistry,
)

from .cluster import ClusterWorkspace, ModelVersion
from .settings import ClusterSettings
from .shell import ClusterError
from .simulation import RunHandle, RunStatus, duration_ru

AGENT_ID = "tnav_cluster"


class TnavClusterAgent(AgentService):
    agent_id = AGENT_ID

    def __init__(
        self,
        root: str | None = None,
        *,
        settings: ClusterSettings | None = None,
        workspace: ClusterWorkspace | None = None,
        poll_seconds: float | None = None,
        progress_every_s: float | None = None,
    ):
        self.settings = settings or (workspace.settings if workspace is not None else ClusterSettings.from_env())
        self.workspace = workspace or ClusterWorkspace(self.settings)
        self.store = SessionStore(prefix="clu", root=root or os.getenv("TNAV_SESSIONS_DIR") or "data/sessions")
        self.tools = ToolRegistry(self.store)
        self.poll_seconds = float(poll_seconds if poll_seconds is not None else self.settings.poll_seconds)
        self.progress_every_s = float(progress_every_s if progress_every_s is not None else self.settings.progress_every_s)
        self.jobs: dict[str, threading.Thread] = {}

    def bind(
        self,
        settings: ClusterSettings,
        *,
        sessions_root: str = "",
        poll_seconds: float | None = None,
        progress_every_s: float | None = None,
    ) -> "TnavClusterAgent":
        """Перенастроить агента на другой кластер: инструменты те же, транспорт и каталог сессий другие.

        Так тесты и лаборатория подключают агента к моку ГД-кластера, не поднимая второй процесс.
        """
        self.settings = settings
        self.workspace = ClusterWorkspace(settings)
        if sessions_root:
            self.store = SessionStore(prefix="clu", root=sessions_root)
            self.tools.store = self.store
        self.poll_seconds = float(poll_seconds if poll_seconds is not None else settings.poll_seconds)
        self.progress_every_s = float(progress_every_s if progress_every_s is not None else settings.progress_every_s)
        return self

    # -- сессия ---------------------------------------------------------------------------------

    def open_session(self, task: dict[str, Any]) -> dict[str, Any]:
        packet = self.packet(task)
        if not packet.objective.strip() and not packet.handoff_message.strip():
            return self.not_opened(
                self.needs_input(
                    {"task_id": packet.task_id},
                    "Опишите задачу для кластера: какую модель считать и какой файл расписания использовать.",
                )
            )
        try:
            connection = self.workspace.check_connection()
        except ClusterError as exc:
            return self.not_opened(
                self.new_result(
                    {"task_id": packet.task_id},
                    "failed",
                    "Гидродинамический кластер не отвечает на подключение, задачу выполнить нельзя. "
                    "Проверьте доступ к кластеру и повторите задачу.",
                    issues=[{"type": "cluster_unreachable", "detail": str(exc)[:200]}],
                )
            )
        models = self.workspace.find_models()
        cards = self._schedule_cards(packet)
        state = self.store.create(
            {
                **self.base_state(packet),
                "cluster": connection,
                "models": [item.model_dump() for item in models],
                "schedule_cards": cards,
                "layout": None,
                "version": None,
                "run": None,
            }
        )
        return self.opened(
            state,
            inspect={
                "cluster": connection,
                "models": [{"model": item.path, "bytes": item.bytes} for item in models[:20]],
                "schedule_files": [
                    {"artifact_id": card["artifact_id"], "filename": card.get("filename") or "", "bytes": card.get("bytes") or 0}
                    for card in cards
                ],
                "simulator": {"program": self.settings.simulator_program(), "options": self.settings.simulator_options()},
            },
        )

    def result(self, state: dict[str, Any]) -> dict[str, Any]:
        stored = state.get("result")
        if isinstance(stored, dict) and stored.get("status"):
            return stored
        version = state.get("version")
        if isinstance(version, dict) and version.get("model_path"):
            return self.version_result(state, ModelVersion.model_validate(version))
        return self.needs_input(
            state,
            "Кластерный агент не понял, что нужно сделать. Напишите, какую модель считать и нужно ли запускать расчёт.",
        )

    # -- итоги ----------------------------------------------------------------------------------

    def version_result(self, state: dict[str, Any], version: ModelVersion) -> dict[str, Any]:
        """Новая версия модели собрана, расчёт не запускался."""
        return self.new_result(
            state,
            "completed",
            "Собрал новую версию модели на кластере: рядом с исходным файлом расписания лежит новый, "
            f"в копии модели путь к расписанию обновлён. Дат в новом расписании: {version.dates_total}.",
            data={"model_version": version.model_dump(), "run_status": "not_started"},
        )

    def run_result(self, state: dict[str, Any], handle: RunHandle, status: RunStatus) -> dict[str, Any]:
        """Итог расчёта для оркестратора: факты, а не «запустил инструмент»."""
        version = state.get("version") if isinstance(state.get("version"), dict) else {}
        data = {
            "run_status": status.state,
            "run_results": {
                "state": status.state,
                "model": handle.model_path,
                "results_dir": handle.results.results_dir,
                "log": handle.results.log,
                "steps": status.digest.steps,
                "elapsed_seconds": status.digest.elapsed_s or status.elapsed_s,
                "last_date": status.digest.current_date,
                "warnings": status.digest.warnings,
                "errors": status.digest.errors[:3],
            },
        }
        if version:
            data["model_version"] = version
        if status.state == "finished":
            elapsed = duration_ru(status.digest.elapsed_s or status.elapsed_s)
            steps = status.digest.steps
            message = (
                f"Расчёт модели на гидродинамическом кластере завершён: {steps} расчётных шагов, "
                f"время расчёта {elapsed}. Результаты лежат в каталоге результатов модели."
            )
            return self.new_result(state, "completed", message, data=data)
        message = (
            "Расчёт модели на гидродинамическом кластере завершился с ошибкой после "
            f"{duration_ru(status.elapsed_s)}. Журнал ошибок расчёта приложен к задаче."
        )
        return self.new_result(
            state,
            "failed",
            message,
            data=data,
            issues=[{"type": "cluster_run_failed", "detail": status.error_tail[-300:]}],
        )

    # -- долгий расчёт --------------------------------------------------------------------------

    def spawn_watch(self, state: dict[str, Any], handle: RunHandle) -> None:
        thread = threading.Thread(
            target=self.watch_run,
            args=(dict(state), handle),
            daemon=True,
            name=f"tnav-run-{handle.run_id}",
        )
        self.jobs[str(state.get("session_id"))] = thread
        thread.start()

    def watch_run(self, state: dict[str, Any], handle: RunHandle) -> dict[str, Any]:
        """Фон: опрашивает кластер, пишет прогресс в ленту, отдаёт итог оркестратору."""
        activity: ActivityClient = self.activity_for_state(state)
        deadline = time.monotonic() + self.settings.max_wait_hours * 3600
        last_line = 0.0
        status = RunStatus()
        while True:
            try:
                status = self.workspace.status(handle)
            except ClusterError as exc:
                activity.trace("Опрос расчёта на кластере не удался", level="warn", detail=str(exc)[:200], run_id=handle.run_id)
                status = RunStatus(state="unknown")
            if status.done:
                break
            now = time.monotonic()
            if now - last_line >= self.progress_every_s:
                activity.progress(status.human_progress(), status="waiting_agent")
                last_line = now
            if now >= deadline:
                final = self.new_result(
                    state,
                    "failed",
                    f"Расчёт на кластере идёт дольше {duration_ru(self.settings.max_wait_hours * 3600)}, "
                    "агент перестал его ждать. На кластере расчёт продолжается.",
                    data={"run_status": "timeout", "run_results": {"state": "running", "model": handle.model_path}},
                    issues=[{"type": "cluster_run_wait_timeout", "detail": handle.run_id}],
                )
                return self._finish(activity, state, final)
            time.sleep(min(self.poll_seconds, max(1.0, deadline - now)))
        self._upload_summary(activity, handle, status)
        return self._finish(activity, state, self.run_result(state, handle, status))

    def _finish(self, activity: ActivityClient, state: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
        try:
            with self.store.lock(str(state["session_id"])):
                fresh = self.store.load(str(state["session_id"]))
                self.store_result(fresh, final)
        except (SessionNotFound, KeyError, ValueError):
            pass  # workflow уже закрыл сессию — итог всё равно уходит оркестратору
        activity.finish_task(final)
        return final

    def _upload_summary(self, activity: ActivityClient, handle: RunHandle, status: RunStatus) -> None:
        """Небольшой файл-итог расчёта в карточки задачи (журнал целиком остаётся на кластере)."""
        if not activity.configured:
            return
        report = "\n".join(
            [
                f"model: {handle.model_path}",
                f"command: {handle.command}",
                f"state: {status.state}",
                f"steps: {status.digest.steps}",
                f"elapsed_seconds: {status.digest.elapsed_s or status.elapsed_s}",
                f"last_date: {status.digest.current_date}",
                f"results_dir: {handle.results.results_dir}",
                "",
                "--- log tail ---",
                status.digest.tail,
                "",
                "--- errors ---",
                status.error_tail,
            ]
        )
        try:
            activity.upload(
                "out_cluster_run",
                f"{handle.model_name}-run.txt",
                report.encode("utf-8"),
                "text/plain; charset=utf-8",
                summary="Журнал расчёта на гидродинамическом кластере",
            )
        except Exception as exc:  # noqa: BLE001 — карточка не должна ломать итог расчёта
            activity.trace("Не удалось приложить журнал расчёта", level="warn", detail=str(exc)[:200])

    # -- служебное ------------------------------------------------------------------------------

    def after_tool(self, state: dict[str, Any], tool_name: str, result: dict[str, Any]) -> None:
        """Короткая строка в ленту инженеру на шагах, которые он ждёт (техническое — в лог разработчика)."""
        if result.get("ok") is False:
            return
        if tool_name == "inspect_model":
            self.activity_for_state(state).progress("Разобрал устройство модели на кластере.")
        elif tool_name == "prepare_model_version":
            self.activity_for_state(state).progress("Собрал новую версию модели с новым расписанием.")

    @staticmethod
    def _schedule_cards(packet: Any) -> list[dict[str, Any]]:
        """Карточки файлов расписания, которые агент может положить на кластер (итог Schedule Builder)."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in packet.cards(role="schedule_out") + packet.cards(suffixes=(".inc", ".sch")):
            artifact_id = str(card.get("artifact_id") or "")
            if not artifact_id or artifact_id in seen:
                continue
            seen.add(artifact_id)
            row = {
                "artifact_id": artifact_id,
                "filename": str(card.get("filename") or ""),
                "bytes": int(card.get("bytes") or 0),
                "role": str(card.get("role") or ""),
            }
            if isinstance(card.get("text"), str) and card["text"].strip():
                row["text"] = card["text"]
                row["bytes"] = row["bytes"] or len(card["text"].encode("utf-8"))
            out.append(row)
        return out


agent = TnavClusterAgent()

from . import agent_tools  # noqa: E402,F401  — регистрация инструментов в реестре агента
