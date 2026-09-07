from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import agent_run
from app.sessions import load_state
from app.tools import execute_tool

GOLDEN_XLSX = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1/MONITORING_well_commissioning_dates.xlsx")
COMBAT = Path("/home/lun1z/omegalul/simulation-model-example/combat-dates-revise/cases")
DATES_XLSX = COMBAT / "case3_half_plus_new.xlsx"
PARAMS_XLSX = COMBAT / "case3_new_wells_params.xlsx"


def _task(objective: str = "даты ввода", **inputs) -> dict:
    return {
        "case_id": "CASE-T",
        "task_id": "TASK-T",
        "agent_id": "excel_extractor",
        "objective": objective,
        "inputs": inputs,
        "context": {},
    }


@pytest.fixture(autouse=True)
def _quiet_activity(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(agent_run, "_post_event", lambda _cid, p: seen.append((str(p.get("kind")), str(p.get("status_message") or ""))))
    agent_run._SEEN_EVENTS = seen  # type: ignore[attr-defined]
    yield seen


def test_open_session_without_workbook_asks_in_prose_and_writes_no_schedule() -> None:
    opened = agent_run.open_session(_task("Extract wells"))
    assert opened["ok"] is False
    result = opened["result"]
    assert result["status"] == "needs_input"
    assert result["issues"][0]["type"] == "missing_excel"
    question = result["requests"][0]["question"]
    assert "Приложите" in question and "{" not in question
    assert "WCONPROD" not in str(result).upper()


def test_open_session_returns_inventory_not_a_capability_decision() -> None:
    if not GOLDEN_XLSX.is_file():
        pytest.skip("golden xlsx missing")
    opened = agent_run.open_session(_task(excel_path=str(GOLDEN_XLSX)))
    assert opened["ok"] is True
    assert "suggested_capability" not in opened
    inspect = opened["inspect"]
    assert inspect["table_count"] >= 1
    table = inspect["tables"][0]
    assert table["columns"] == ["Скважина", "Дата ввода"]
    assert table["sample"] and table["sample"][0]["Скважина"] == "1601"
    assert table["file"] == GOLDEN_XLSX.name
    assert opened["files"] == [GOLDEN_XLSX.name]
    assert agent_run.session_result(opened["session_id"])["status"] == "needs_input"


def test_extract_commissioning_needs_explicit_columns_and_validates_them() -> None:
    if not GOLDEN_XLSX.is_file():
        pytest.skip("golden xlsx missing")
    opened = agent_run.open_session(_task(excel_path=str(GOLDEN_XLSX)))
    state = load_state(opened["session_id"])
    table_id = opened["inspect"]["tables"][0]["table_id"]

    incomplete = execute_tool(state, "extract_commissioning", {"table_id": table_id})
    assert incomplete["ok"] is False
    assert incomplete["error"]["code"] == "spec_incomplete"
    assert incomplete["error"]["details"]["missing"] == ["well_column", "date_column"]
    assert incomplete["error"]["details"]["available_tables"][0]["table_id"] == table_id

    wrong = execute_tool(state, "extract_commissioning", {"table_id": table_id, "well_column": "Скважина", "date_column": "Нет такой"})
    assert wrong["ok"] is False
    assert wrong["error"]["code"] == "column_not_found"
    assert wrong["error"]["details"]["available_columns"] == ["Скважина", "Дата ввода"]

    not_dates = execute_tool(state, "extract_commissioning", {"table_id": table_id, "well_column": "Дата ввода", "date_column": "Скважина"})
    assert not_dates["ok"] is False
    assert not_dates["error"]["code"] == "column_not_dates"

    # The result is not stored until a valid extraction happened.
    assert agent_run.session_result(opened["session_id"])["status"] == "needs_input"


def test_extract_commissioning_is_deterministic_and_summary_is_prose() -> None:
    if not GOLDEN_XLSX.is_file():
        pytest.skip("golden xlsx missing")
    opened = agent_run.open_session(_task(excel_path=str(GOLDEN_XLSX)))
    state = load_state(opened["session_id"])
    table_id = opened["inspect"]["tables"][0]["table_id"]
    done = execute_tool(state, "extract_commissioning", {"table_id": table_id, "well_column": "скважина", "date_column": "Дата ввода"})
    assert done["ok"] is True, done
    assert done["result"]["status"] == "completed"
    result = agent_run.session_result(opened["session_id"])
    assert result["status"] == "completed"
    facts = result["data"]["facts"]
    assert {item["well"] for item in facts} >= {"1601", "1602"}
    for item in facts:
        assert "T" not in str(item["date"])
        assert len(str(item["date"])) == 10
    assert result["data"]["total_count"] == len(facts)
    assert result["data"]["preview"] == facts[:10]
    assert result["data"]["commissioning_source"]["well_column"] == "Скважина"
    assert result["data"]["file_name"] == GOLDEN_XLSX.name
    assert result["message"].startswith("Даты ввода: 2 скважины (1601, 1602)")
    kinds = [kind for kind, _ in agent_run._SEEN_EVENTS]  # type: ignore[attr-defined]
    assert "agent.accepted" in kinds
    assert "agent.result" not in kinds, "the orchestrator emits the single agent.result"
    assert any(msg.startswith("Даты ввода:") for _, msg in agent_run._SEEN_EVENTS)  # type: ignore[attr-defined]


def test_two_workbooks_become_one_session_with_per_file_inventory(monkeypatch) -> None:
    if not (DATES_XLSX.is_file() and PARAMS_XLSX.is_file()):
        pytest.skip("combat fixtures missing")
    monkeypatch.setattr(
        agent_run,
        "_excel_cards",
        lambda _task: [
            {"artifact_id": "excel", "filename": DATES_XLSX.name, "path": str(DATES_XLSX)},
            {"artifact_id": "excel_1", "filename": PARAMS_XLSX.name, "path": str(PARAMS_XLSX)},
        ],
    )
    opened = agent_run.open_session(_task("даты ввода и новые скважины"))
    assert opened["ok"] is True
    assert opened["files"] == [DATES_XLSX.name, PARAMS_XLSX.name]
    by_file = {t["file"]: t for t in opened["inspect"]["tables"]}
    assert set(by_file) == {DATES_XLSX.name, PARAMS_XLSX.name}
    dates, params = by_file[DATES_XLSX.name], by_file[PARAMS_XLSX.name]
    assert dates["columns"] == ["Скважина", "Дата ввода"]
    assert "MD_TOP" in params["columns"] and "WELLTRACK" in params["columns"]

    state = load_state(opened["session_id"])
    first = execute_tool(state, "extract_commissioning", {"table_id": dates["table_id"], "well_column": "Скважина", "date_column": "Дата ввода"})
    assert first["ok"] is True
    mapping = {
        "date": "Дата ввода",
        "group": "Группа",
        "phase": "Фаза",
        "i": "I",
        "j": "J",
        "md_top": "MD_TOP",
        "md_bot": "MD_BOT",
        "diameter": "Диаметр",
        "control": "Режим",
        "rate": "Дебит",
        "bhp": "BHP",
        "vfp_table": "VFP",
        "welltrack_include": "WELLTRACK",
    }
    # n8n passes optional JSON tool arguments as strings.
    second = execute_tool(state, "extract_well_parameters", {"table_id": params["table_id"], "well_column": "Скважина", "mapping": json.dumps(mapping)})
    assert second["ok"] is True, second
    result = agent_run.session_result(opened["session_id"])
    assert result["status"] == "completed"
    assert len(result["data"]["facts"]) == 14
    new_wells = result["data"]["new_wells"]
    assert [row["well"] for row in new_wells][:3] == ["N001", "N002", "N003"]
    assert new_wells[0] == {
        "well": "N001",
        "date": "2023-01-01",
        "group": "GNEW",
        "phase": "OIL",
        "i": 1,
        "j": 1,
        "md_top": 3200,
        "md_bot": 3240,
        "diameter": 0.15,
        "control": "GRAT",
        "rate": 80000,
        "bhp": 90,
        "vfp_table": 30,
        "welltrack_include": "INCLUDE/WELLTRACK/N001_WELLTRACK.INC",
    }
    assert result["message"].startswith("Даты ввода: 14 скважин")
    assert "Параметры новых скважин: 10 скважин" in result["message"]
    assert result["data"]["files"] == [DATES_XLSX.name, PARAMS_XLSX.name]


def test_extract_well_parameters_passes_unmapped_columns_under_their_headers() -> None:
    if not PARAMS_XLSX.is_file():
        pytest.skip("combat fixtures missing")
    opened = agent_run.open_session(_task("новые скважины", excel_path=str(PARAMS_XLSX)))
    state = load_state(opened["session_id"])
    table_id = opened["inspect"]["tables"][0]["table_id"]
    bad = execute_tool(state, "extract_well_parameters", {"table_id": table_id, "well_column": "Скважина", "mapping": {"group": "Нет колонки"}})
    assert bad["ok"] is False and bad["error"]["code"] == "column_not_found"
    done = execute_tool(state, "extract_well_parameters", {"table_id": table_id, "well_column": "Скважина"})
    assert done["ok"] is True
    row = agent_run.session_result(opened["session_id"])["data"]["new_wells"][0]
    assert row["well"] == "N001"
    assert row["Группа"] == "GNEW" and row["MD_TOP"] == 3200  # Schedule Builder resolves these via its aliases


def test_repeated_extraction_attempts_are_bounded() -> None:
    if not GOLDEN_XLSX.is_file():
        pytest.skip("golden xlsx missing")
    opened = agent_run.open_session(_task(excel_path=str(GOLDEN_XLSX)))
    state = load_state(opened["session_id"])
    table_id = opened["inspect"]["tables"][0]["table_id"]
    for _ in range(agent_run.MAX_TOOL_REPEATS):
        execute_tool(state, "extract_commissioning", {"table_id": table_id, "well_column": "Скважина", "date_column": "нет"})
    blocked = execute_tool(state, "extract_commissioning", {"table_id": table_id, "well_column": "Скважина", "date_column": "Дата ввода"})
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "too_many_attempts"


def test_ask_engineer_requires_prose_and_stores_needs_input() -> None:
    if not GOLDEN_XLSX.is_file():
        pytest.skip("golden xlsx missing")
    opened = agent_run.open_session(_task(excel_path=str(GOLDEN_XLSX)))
    state = load_state(opened["session_id"])
    machine = execute_tool(state, "ask_engineer", {"question": "date_column=? {choose}"})
    assert machine["ok"] is False
    assert machine["error"]["code"] == "question_not_human"
    asked = execute_tool(
        state,
        "ask_engineer",
        {
            "question": "В таблице две колонки с датами — «Дата ввода» и «Дата ввода (baseline в .INC)». Какую из них считать новой датой ввода?",
            "options": "Дата ввода; Дата ввода (baseline в .INC)",
            "topic": "date_column",
        },
    )
    assert asked["ok"] is True
    result = agent_run.session_result(opened["session_id"])
    assert result["status"] == "needs_input"
    request = result["requests"][0]
    assert request["question_id"] == "Q-date_column"
    assert [opt["label"] for opt in request["options"]] == ["Дата ввода", "Дата ввода (baseline в .INC)"]
    assert request["accepts"]["free_text"] is True


def test_excel_cards_collect_every_workbook_of_the_case(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_run,
        "_fetch_case_state",
        lambda _activity, _case: {
            "artifacts": {
                "excel": {"artifact_id": "excel", "filename": "dates.xlsx", "role": "excel"},
                "schedule": {"source": {"artifact_id": "schedule_source", "filename": "baseline.inc", "role": "schedule_source"}},
                "attachments": [{"artifact_id": "excel_1", "filename": "params.xlsx", "role": "excel"}],
            }
        },
    )
    cards = agent_run._excel_cards(
        {
            "case_id": "CASE-9",
            "inputs": {"activity_base_url": "http://mas-activity:8200", "artifact_ids": ["excel", "excel_1", "schedule_source"]},
        }
    )
    assert [(c["artifact_id"], c["filename"]) for c in cards] == [("excel", "dates.xlsx"), ("excel_1", "params.xlsx")]
    assert cards[1]["url"] == "http://mas-activity:8200/cases/CASE-9/artifacts/excel_1"

    # Older orchestrator states kept only the last workbook in the `excel` slot (CASE-6a9e67ef): the fixed
    # id `excel` is probed anyway so the first workbook is never lost.
    monkeypatch.setattr(agent_run, "_fetch_case_state", lambda _a, _c: {"artifacts": {"excel": {"artifact_id": "excel_1", "filename": "params.xlsx", "role": "excel"}}})
    cards = agent_run._excel_cards({"case_id": "CASE-9", "inputs": {"activity_base_url": "http://mas-activity:8200", "artifact_ids": ["excel_1"]}})
    assert [c["artifact_id"] for c in cards] == ["excel", "excel_1"]


def test_emit_tool_progress_for_detect_tables_and_tool_errors() -> None:
    seen: list[str] = []
    state = {"payload": {"case_id": "CASE-T", "task_id": "T1", "activity_base": "http://x"}}
    agent_run._post_event = lambda _case_id, payload: seen.append(str(payload.get("status_message") or ""))  # type: ignore[method-assign]
    agent_run.emit_tool_progress(state, "detect_tables", {"ok": True, "result": {"tables": [{}, {}]}})
    agent_run.emit_tool_progress(state, "extract_commissioning", {"ok": False, "error": {"code": "column_not_dates"}})
    agent_run.emit_tool_progress(state, "extract_commissioning", {"ok": True, "result": {"status": "completed"}})
    assert seen == ["Нашёл таблиц: 2", "Выбранная колонка не похожа на даты — подбираю другую"]


def test_date_normalisation_accepts_engineering_formats() -> None:
    assert agent_run._as_date("2020-02-23T00:00:00") == ("2020-02-23", True)
    assert agent_run._as_date("23.02.2020") == ("2020-02-23", True)
    assert agent_run._as_date("23 FEB 2020") == ("23 FEB 2020", True)
    assert agent_run._as_date("Примечание") == ("Примечание", False)
    assert agent_run._as_well(1601.0) == "1601"
    assert agent_run._as_well("1601.0") == "1601"
