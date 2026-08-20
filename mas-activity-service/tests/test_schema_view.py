"""Schema frames: input → orchestrator → agents → result timeline."""

from __future__ import annotations

from app.schema_view import END_LABEL, START_LABEL, build_schema_frames, build_schema_model


def _event(kind: str, **kwargs):
    payload = {"kind": kind, "actor": kwargs.pop("actor", "orchestrator"), **kwargs}
    payload.setdefault("event_id", kind)
    return payload


def test_empty_events_start_at_task_statement() -> None:
    frames = build_schema_frames([], state={"goal": "Обновить даты"})
    assert frames[0]["label"] == START_LABEL
    assert frames[0]["nodes"]["input"]["tone"] == "active"
    assert frames[0]["input"]["goal"] == "Обновить даты"


def test_handoff_lights_edge_and_progress_shows_agent_bubble() -> None:
    events = [
        _event("case.created", actor="user", status_message="Принял задачу: даты ввода", payload={"files": ["wells.xlsx"]}),
        _event("orchestrator.status", status_message="Выполняю декомпозицию задачи и выбираю ответственных"),
        _event(
            "agent.handoff",
            agent_id="excel_extractor",
            status_message="Передаю Excel",
            handoff_message="Агент Excel, достань из файла данные по датам ввода скважин.",
        ),
        _event("agent.accepted", actor="excel_extractor", agent_id="excel_extractor", status_message="Разбираю Excel"),
        _event(
            "agent.progress",
            actor="excel_extractor",
            agent_id="excel_extractor",
            status_message="Читаю лист с датами ввода",
        ),
        _event("agent.result", actor="excel_extractor", agent_id="excel_extractor", status_message="Таблица готова"),
        _event(
            "agent.handoff",
            agent_id="schedule_builder",
            handoff_message="Schedule, замени даты ввода скважин в исходном файле.",
        ),
        _event(
            "agent.progress",
            actor="schedule_builder",
            agent_id="schedule_builder",
            status_message="Заменяю даты ввода скважин в исходном файле schedule",
        ),
        _event("case.finished", status_message="Готово: даты обновлены", payload={"result": "SCHEDULE записан"}),
    ]
    frames = build_schema_frames(events, state={"goal": "Обновить даты ввода", "artifacts": {"excel": {"filename": "wells.xlsx"}}})
    assert frames[0]["label"] == START_LABEL
    assert frames[0]["edges"]["in_orch"]["tone"] == "active"
    assert "wells.xlsx" in frames[0]["input"]["files"]

    orch = frames[1]
    assert orch["nodes"]["orchestrator"]["tone"] == "active"
    assert orch["nodes"]["orchestrator"]["bubble"] == "Выполняю декомпозицию задачи и выбираю ответственных"
    assert orch["nodes"]["orchestrator"]["caption"] == "Выполняю декомпозицию задачи и выбираю ответственных"

    handoff = frames[2]
    assert handoff["edges"]["orch_excel"]["tone"] == "active"
    assert handoff["edges"]["orch_excel"]["bubble"].startswith("Агент Excel")
    assert handoff["nodes"]["excel"]["tone"] == "pending"

    progress = frames[4]
    assert progress["nodes"]["excel"]["tone"] == "active"
    assert progress["nodes"]["excel"]["bubble"] == "Читаю лист с датами ввода"
    assert progress["nodes"]["excel"]["caption"] == "Читаю лист с датами ввода"
    assert progress["nodes"]["orchestrator"]["bubble"] is None
    assert progress["nodes"]["orchestrator"]["caption"] == "Передаю Excel"
    assert progress["edges"]["orch_excel"]["bubble"].startswith("Агент Excel")

    excel_done = frames[5]
    assert excel_done["nodes"]["excel"]["tone"] == "done"
    assert excel_done["nodes"]["excel"]["caption"] == "Таблица готова"

    sched = frames[7]
    assert sched["nodes"]["schedule"]["tone"] == "active"
    assert sched["nodes"]["schedule"]["bubble"].startswith("Заменяю даты ввода")
    assert sched["nodes"]["schedule"]["caption"].startswith("Заменяю даты ввода")

    done = frames[-1]
    assert done["label"] == END_LABEL
    assert done["nodes"]["output"]["tone"] == "active"
    assert done["output"]["result"] == "SCHEDULE записан"
    assert done["output"]["prompt"] == "Готово: даты обновлены"
    assert done["edges"]["orch_out"]["tone"] == "active"
    assert done["nodes"]["orchestrator"]["caption"] == "Готово: даты обновлены"
    assert done["nodes"]["excel"]["caption"] == "Таблица готова"

    model = build_schema_model(events, state={"goal": "x"}, status="done")
    assert model["complete"] is True
    assert model["start_label"] == START_LABEL
    assert model["end_label"] == END_LABEL


def test_hitl_uses_user_node() -> None:
    events = [
        _event("case.created", actor="user"),
        _event("hitl.request", status_message="Какой корневой INCLUDE главный?"),
        _event("hitl.answered", actor="user", status_message="Пользователь ответил: MAIN.INC"),
    ]
    frames = build_schema_frames(events)
    ask = frames[1]
    assert ask["nodes"]["user"]["tone"] == "active"
    assert ask["nodes"]["user"]["caption"] == "Какой корневой INCLUDE главный?"
    assert ask["nodes"]["orchestrator"]["tone"] == "waiting"
    assert ask["edges"]["orch_user"]["bubble"] == "Какой корневой INCLUDE главный?"
    answered = frames[2]
    assert answered["nodes"]["orchestrator"]["tone"] == "active"
    assert answered["nodes"]["user"]["caption"] == "Какой корневой INCLUDE главный?"
    assert answered["edges"]["user_orch"]["tone"] == "active"
