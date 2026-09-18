"""Инструменты кластерного агента для LLM: осмотр модели, новая версия, запуск и наблюдение расчёта.

Каждый инструмент — это маршрут ``POST /agent-tools/<имя>``, который вызывает workflow агента в n8n.
Правила такие же, как у остальных агентов MAS:

* считают инструменты, модель только выбирает инструмент и аргументы;
* ошибка аргументов — ``ToolError`` для модели (код + подсказка), а не вопрос инженеру;
* вопрос инженеру — только ``ask_engineer``, русской прозой;
* результат сессии фиксируется один раз (``store_result``), дальше модель останавливается.
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Any

from mas_agent_kit import ToolError, engineer_request_from_args, in_progress

from .agent import agent
from .cluster import ModelLayout
from .shell import ClusterError
from .simulation import RunHandle, duration_ru

#: Сколько ждать между опросами расчёта, если инженер спросит «как идёт» (подсказка для ленты).
POLL_HINT = "10m"


def _save(state: dict[str, Any]) -> None:
    agent.store.save(state)


def _models(state: dict[str, Any]) -> list[str]:
    return [str(item.get("path") or "") for item in (state.get("models") or []) if isinstance(item, dict)]


def _layout(state: dict[str, Any], model: Any = None) -> ModelLayout:
    """Разобранная модель из сессии; если модель названа в аргументах — разобрать её."""
    wanted = str(model or "").strip()
    stored = state.get("layout") if isinstance(state.get("layout"), dict) else None
    if stored and (not wanted or stored.get("data_path") == wanted):
        return ModelLayout.model_validate(stored)
    if not wanted:
        raise ToolError(
            "model_not_chosen",
            "Сначала выбери модель: вызови inspect_model с путём к входному файлу модели из списка.",
            available_models=_models(state)[:20],
        )
    layout = _inspect(state, wanted)
    return layout


def _inspect(state: dict[str, Any], model: str) -> ModelLayout:
    try:
        layout = agent.workspace.layout(model)
    except ClusterError as exc:
        raise ToolError(
            "model_not_found",
            f"Модель на кластере не разобрать: {exc}. Возьми путь из списка моделей или уточни его у инженера.",
            available_models=_models(state)[:20],
        ) from exc
    state["layout"] = layout.model_dump()
    _save(state)
    return layout


def _schedule_payload(state: dict[str, Any], artifact_id: Any = None) -> tuple[bytes, str]:
    """Новый файл schedule: из карточки задачи (текст) или загрузкой из Activity."""
    cards = [card for card in (state.get("schedule_cards") or []) if isinstance(card, dict)]
    wanted = str(artifact_id or "").strip()
    if not cards:
        raise ToolError(
            "schedule_file_missing",
            "К задаче не приложен новый файл расписания. Спроси инженера через ask_engineer, откуда взять расписание, "
            "или сообщи в итоге, что расписание не передано.",
        )
    if wanted:
        card = next((item for item in cards if str(item.get("artifact_id")) == wanted), None)
        if card is None:
            raise ToolError(
                "schedule_file_unknown",
                "Такого файла расписания в задаче нет — выбери один из доступных.",
                available_files=[str(item.get("artifact_id")) for item in cards],
            )
    elif len(cards) == 1:
        card = cards[0]
    else:
        raise ToolError(
            "schedule_file_ambiguous",
            "В задаче несколько файлов расписания — укажи, какой класть на кластер.",
            available_files=[str(item.get("artifact_id")) for item in cards],
        )
    filename = str(card.get("filename") or "")
    text = card.get("text")
    if isinstance(text, str) and text.strip():
        return text.encode("utf-8"), filename
    try:
        payload, downloaded = agent.activity_for_state(state).download(str(card.get("artifact_id")))
    except Exception as exc:  # noqa: BLE001 — сеть/Activity: это ошибка для модели, не для инженера
        raise ToolError(
            "schedule_file_unavailable",
            "Файл расписания не скачался из задачи; попробуй другой файл или сообщи об этом в итоге.",
            detail=str(exc)[:200],
        ) from exc
    return payload, filename or downloaded


def _run_handle(state: dict[str, Any]) -> RunHandle:
    run = state.get("run")
    if not isinstance(run, dict) or not run.get("model_path"):
        raise ToolError(
            "run_not_started",
            "Расчёт в этой задаче ещё не запускался — сначала start_calculation.",
        )
    return RunHandle.model_validate(run)


@agent.tools.tool(
    "list_models",
    "Показать входные файлы моделей, которые лежат в рабочем каталоге гидродинамического кластера.",
    {},
)
def list_models(ctx, args: dict[str, Any]) -> dict[str, Any]:
    try:
        models = agent.workspace.find_models()
    except ClusterError as exc:
        raise ToolError("cluster_unavailable", f"Кластер не ответил: {exc}") from exc
    ctx.state["models"] = [item.model_dump() for item in models]
    _save(ctx.state)
    return {"models": [{"model": item.path, "bytes": item.bytes} for item in models[:20]], "count": len(models)}


@agent.tools.tool(
    "inspect_model",
    "Разобрать модель на кластере: секции входного файла, подключённые файлы и основной файл расписания "
    "(в нём больше всего строк с датами). Детерминированно, без расчёта.",
    {"model": {"type": "string", "description": "Путь к входному файлу модели относительно рабочего каталога кластера."}},
    required=["model"],
)
def inspect_model(ctx, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args.get("model") or "").strip()
    if not model:
        raise ToolError("model_not_chosen", "Назови путь к входному файлу модели.", available_models=_models(ctx.state)[:20])
    layout = _inspect(ctx.state, model)
    view = layout.inspect_view()
    if layout.main_schedule is None:
        view["next_step"] = (
            "Файл расписания в секции расписания не найден: спроси инженера через ask_engineer, какой файл считать основным."
        )
    else:
        view["next_step"] = "Модель разобрана. Дальше prepare_model_version, если нужна новая версия модели с новым расписанием."
    return view


@agent.tools.tool(
    "prepare_model_version",
    "Собрать новую версию модели: положить новый файл расписания рядом со старым, скопировать входной файл "
    "модели и переписать в копии путь подключения расписания. Ничего не удаляет.",
    {
        "version_name": {"type": "string", "description": "Короткое латинское имя версии для имён файлов, например prognoz_2027."},
        "model": {"type": "string", "description": "Путь к входному файлу модели; по умолчанию — разобранная модель."},
        "schedule_artifact_id": {"type": "string", "description": "Какой файл расписания из задачи положить на кластер; по умолчанию единственный."},
    },
)
def prepare_model_version(ctx, args: dict[str, Any]) -> dict[str, Any]:
    layout = _layout(ctx.state, args.get("model"))
    if layout.main_schedule is None:
        raise ToolError(
            "schedule_include_not_found",
            "В модели не найден файл расписания, подключённый в секции расписания — уточни у инженера через ask_engineer, "
            "какой файл считать основным.",
            sections=layout.sections,
        )
    payload, filename = _schedule_payload(ctx.state, args.get("schedule_artifact_id"))
    from .model_files import count_dates  # noqa: PLC0415 — локальная проверка содержимого

    if count_dates(payload.decode("utf-8", errors="replace")) == 0:
        raise ToolError(
            "schedule_file_without_dates",
            "В присланном файле нет строк с датами — похоже, это не файл расписания. Выбери другой файл задачи.",
            bytes=len(payload),
        )
    version_name = str(args.get("version_name") or "").strip() or f"prognoz_{time.strftime('%Y%m%d')}"
    try:
        version = agent.workspace.create_version(layout, version_name, payload, schedule_filename=PurePosixPath(filename).name if filename else "")
    except ClusterError as exc:
        raise ToolError(
            "version_not_created",
            f"Новую версию модели собрать не удалось: {exc}. Попробуй другое имя версии или уточни задачу у инженера.",
        ) from exc
    ctx.state["version"] = version.model_dump()
    _save(ctx.state)
    return {
        "status": "prepared",
        "model": version.model_path,
        "schedule": version.schedule_path,
        "dates": version.dates_total,
        "next_step": "Новая версия модели собрана. Если задача просит расчёт — start_calculation; иначе заверши ответ одним предложением.",
    }


@agent.tools.tool(
    "start_calculation",
    "Запустить расчёт модели на кластере командой tNavigator из настроек сервиса. Возвращает статус «в работе»: "
    "результат придёт позже, когда расчёт закончится.",
    {"model": {"type": "string", "description": "Путь к входному файлу модели; по умолчанию — собранная новая версия."}},
)
def start_calculation(ctx, args: dict[str, Any]) -> dict[str, Any]:
    agent.tools.result_guard(ctx.state)
    version = ctx.state.get("version") if isinstance(ctx.state.get("version"), dict) else {}
    layout = ctx.state.get("layout") if isinstance(ctx.state.get("layout"), dict) else {}
    model = str(args.get("model") or "").strip() or str(version.get("model_path") or "") or str(layout.get("data_path") or "")
    if not model:
        raise ToolError(
            "model_not_chosen",
            "Непонятно, какую модель считать: сначала inspect_model (и prepare_model_version, если нужна новая версия).",
            available_models=_models(ctx.state)[:20],
        )
    dates = int(version.get("dates_total") or 0) or int((layout.get("main_schedule") or {}).get("dates") or 0)
    try:
        handle = agent.workspace.launch(model, dates_total=dates)
    except ClusterError as exc:
        raise ToolError(
            "run_not_started",
            f"Расчёт запустить не удалось: {exc}. Проверь путь к модели или сообщи об этом в итоге.",
        ) from exc
    ctx.state["run"] = handle.model_dump()
    message = "Расчёт модели запущен на гидродинамическом кластере. Сообщу, когда он закончится."
    agent.store_result(
        ctx.state,
        in_progress(
            agent.agent_id,
            str(ctx.state.get("task_id") or ""),
            message,
            watch={"kind": "cluster_run", "ref": handle.run_id, "poll_hint": POLL_HINT},
        ),
    )
    agent.spawn_watch(ctx.state, handle)
    return {"status": "in_progress", "model": handle.model_path, "results_dir": handle.results.results_dir, "message": message}


@agent.tools.tool(
    "check_calculation",
    "Состояние запущенного расчёта прямо сейчас: идёт, завершился или упал, сколько пройдено шагов и сколько осталось.",
    {},
)
def check_calculation(ctx, args: dict[str, Any]) -> dict[str, Any]:
    handle = _run_handle(ctx.state)
    try:
        status = agent.workspace.status(handle)
    except ClusterError as exc:
        raise ToolError("cluster_unavailable", f"Кластер не ответил на опрос расчёта: {exc}") from exc
    return {
        "state": status.state,
        "steps": status.digest.steps,
        "last_date": status.digest.current_date,
        "elapsed": duration_ru(status.elapsed_s),
        "eta": duration_ru(status.eta_s) if status.eta_s else "",
        "errors": status.digest.errors[:3],
        "next_step": (
            "Расчёт идёт, результат придёт позже через Activity — больше инструменты не вызывай."
            if status.state in ("running", "starting")
            else "Расчёт закончился; итог уже зафиксирован наблюдателем расчёта."
        ),
    }


@agent.tools.tool(
    "ask_engineer",
    "Задать инженеру один вопрос по-русски, когда без ответа нельзя выбрать модель, версию или файл расписания. "
    "Не для ошибок вызова инструментов.",
    {
        "question": {"type": "string", "description": "Обычная русская фраза без имён полей и путей."},
        "options": {"type": "string", "description": "Варианты через точку с запятой, как их видит инженер; пусто — свободный ответ."},
    },
    required=["question"],
)
def ask_engineer(ctx, args: dict[str, Any]) -> dict[str, Any]:
    agent.tools.repeat_guard(ctx.state, "ask_engineer", limit=3)
    agent.tools.result_guard(ctx.state)
    request = engineer_request_from_args(args, default_topic="cluster", default_files=("inc",))
    result = agent.new_result(
        ctx.state,
        "needs_input",
        request["question"],
        requests=[request],
        issues=[{"type": "engineer_input_required", "topic": request["question_id"]}],
    )
    agent.store_result(ctx.state, result)
    return {"status": "needs_input", "question_id": request["question_id"]}
