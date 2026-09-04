from __future__ import annotations

from app.agent_run import commissioning_facts, run_excel_agent


def test_excel_agent_asks_for_workbook_without_writing_schedule() -> None:
    result = run_excel_agent(
        {
            "case_id": "CASE-1",
            "task_id": "TASK-1",
            "agent_id": "excel_extractor",
            "objective": "Extract wells",
            "inputs": {},
            "context": {},
        }
    )
    assert result["status"] == "needs_input"
    assert result["issues"][0]["type"] == "missing_excel"
    blob = str(result).upper()
    assert "WCONPROD" not in blob
    assert "SCHEDULE" not in blob or "Нет Excel" in result["message"]


def test_commissioning_facts_from_russian_columns() -> None:
    facts = commissioning_facts(
        [
            {
                "columns": ["Скважина", "Дата ввода"],
                "preview": [{"Скважина": "1601", "Дата ввода": "23 FEB 2020"}],
            }
        ]
    )
    assert facts == [{"well": "1601", "date": "23 FEB 2020"}]


def test_commissioning_facts_normalizes_numeric_well_and_iso_date() -> None:
    facts = commissioning_facts(
        [
            {
                "columns": ["Well", "Дата ввода"],
                "preview": [{"Well": 1601.0, "Дата ввода": "2020-02-23"}],
            }
        ]
    )
    assert facts[0]["well"] == "1601"
    assert facts[0]["date"] == "2020-02-23"


def test_commissioning_facts_strips_iso_datetime() -> None:
    facts = commissioning_facts(
        [
            {
                "columns": ["Скважина", "Дата ввода"],
                "preview": [{"Скважина": "1601", "Дата ввода": "2020-02-23T00:00:00"}],
            }
        ]
    )
    assert facts[0]["date"] == "2020-02-23"


def test_golden_case_1_excel_extracts_well_date_facts(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    xlsx = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1/MONITORING_well_commissioning_dates.xlsx")
    if not xlsx.is_file():
        return
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    result = run_excel_agent(
        {
            "case_id": "CASE-G1",
            "task_id": "TASK-G1",
            "agent_id": "excel_extractor",
            "objective": "даты ввода",
            "inputs": {"excel_path": str(xlsx)},
            "context": {},
        }
    )
    assert result["status"] == "completed"
    facts = result["data"]["facts"]
    assert len(facts) >= 2
    wells = {item["well"] for item in facts}
    assert {"1601", "1602"} <= wells
    assert all(item.get("date") for item in facts)
    from datetime import date as date_cls

    for item in facts:
        raw = str(item["date"])
        assert "T" not in raw
        date_cls.fromisoformat(raw)


def test_excel_agent_fetches_workbook_from_activity_artifact_url(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    xlsx = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1/MONITORING_well_commissioning_dates.xlsx")
    if not xlsx.is_file():
        return
    seen: dict[str, str] = {}

    def fake_fetch(url: str) -> bytes:
        seen["url"] = url
        return xlsx.read_bytes()

    monkeypatch.setattr("app.agent_run._fetch_bytes", fake_fetch)
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    result = run_excel_agent(
        {
            "case_id": "CASE-1",
            "task_id": "TASK-1",
            "agent_id": "excel_extractor",
            "objective": "даты ввода",
            "inputs": {
                "activity_base_url": "http://mas-activity:8200",
                "artifacts": {"excel": {"filename": "dates.xlsx", "artifact_id": "excel"}},
            },
            "context": {},
        }
    )
    assert seen["url"] == "http://mas-activity:8200/cases/CASE-1/artifacts/excel"
    assert result["status"] == "completed"
    wells = {item["well"] for item in result["data"]["facts"]}
    assert {"1601", "1602"} <= wells


def test_open_session_then_extract_commissioning_matches_agent_run(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from app.agent_run import extract_commissioning, open_session, session_result

    xlsx = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1/MONITORING_well_commissioning_dates.xlsx")
    if not xlsx.is_file():
        return
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    task = {
        "case_id": "CASE-G1",
        "task_id": "TASK-G1",
        "agent_id": "excel_extractor",
        "objective": "даты ввода",
        "inputs": {"excel_path": str(xlsx)},
        "context": {},
    }
    opened = open_session(task)
    assert opened["ok"] is True
    assert opened["suggested_capability"] == "commissioning"
    assert opened["inspect"]["table_count"] >= 1
    extracted = extract_commissioning(opened["session_id"])
    assert extracted["status"] == "completed"
    wells = {item["well"] for item in extracted["data"]["facts"]}
    assert {"1601", "1602"} <= wells
    fetched = session_result(opened["session_id"])
    assert fetched["data"]["facts"] == extracted["data"]["facts"]
    assert extracted["data"]["total_count"] == len(extracted["data"]["facts"])
    assert extracted["data"]["preview"] == extracted["data"]["facts"][:10]


def test_open_and_extract_emit_live_activity_status_lines(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from app import agent_run

    xlsx = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1/MONITORING_well_commissioning_dates.xlsx")
    if not xlsx.is_file():
        return
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    seen: list[tuple[str, str]] = []

    def capture(case_id, payload):
        seen.append((str(payload.get("kind")), str(payload.get("status_message") or "")))

    monkeypatch.setattr(agent_run, "_post_event", capture)
    task = {
        "case_id": "CASE-LIVE",
        "task_id": "TASK-LIVE",
        "agent_id": "excel_extractor",
        "objective": "даты ввода",
        "inputs": {"excel_path": str(xlsx)},
        "context": {},
    }
    opened = agent_run.open_session(task)
    assert opened["ok"] is True
    extracted = agent_run.extract_commissioning(opened["session_id"])
    assert extracted["status"] == "completed"
    kinds = [kind for kind, _ in seen]
    messages = [msg for _, msg in seen]
    assert "agent.accepted" in kinds
    assert "Разбираю Excel" in messages
    assert any(msg.startswith("Файл ") and "таблиц" in msg for msg in messages)
    assert any(msg.startswith("Нашёл таблиц:") for msg in messages)
    assert any(msg.startswith("Фактов скважина+дата:") for msg in messages)
    assert "agent.result" in kinds
    assert any("Извлечено таблиц" in msg for msg in messages)


def test_emit_tool_progress_for_detect_tables() -> None:
    from app.agent_run import emit_tool_progress

    seen: list[str] = []

    def capture(_case_id, payload):
        seen.append(str(payload.get("status_message") or ""))

    import app.agent_run as agent_run

    agent_run._post_event = capture  # type: ignore[method-assign]
    emit_tool_progress(
        {"payload": {"case_id": "CASE-T", "task_id": "T1", "activity_base": "http://x"}},
        "detect_tables",
        {"ok": True, "result": {"tables": [{}, {}]}},
    )
    assert seen == ["Нашёл таблиц: 2"]


def test_suggested_capability_dates_vs_rates() -> None:
    from app.agent_run import suggested_capability

    assert suggested_capability("даты ввода скважин") == "commissioning"
    assert suggested_capability("сдвинуть даты ввода") == "commissioning"
    assert suggested_capability("commissioning dates") == "commissioning"
    assert suggested_capability("достань дебиты") == "operations"
    assert suggested_capability("PVT и SCAL") == "operations"
    assert suggested_capability("Extract wells") == "operations"
    assert suggested_capability("") == "operations"


def test_session_result_needs_input_without_extract_then_close(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from app.agent_run import open_session, session_result
    from app.sessions import close_session

    xlsx = Path("/home/lun1z/omegalul/simulation-model-example/golden-cases/golden_case_1/MONITORING_well_commissioning_dates.xlsx")
    if not xlsx.is_file():
        return
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    opened = open_session(
        {
            "case_id": "CASE-IDLE",
            "task_id": "TASK-IDLE",
            "agent_id": "excel_extractor",
            "objective": "достань дебиты",
            "inputs": {"excel_path": str(xlsx)},
            "context": {},
        }
    )
    assert opened["ok"] is True
    assert opened["suggested_capability"] == "operations"
    result = session_result(opened["session_id"])
    assert result["status"] == "needs_input"
    assert result["issues"][0]["type"] == "no_extract"
    closed = close_session(opened["session_id"])
    assert closed["ok"] is True
    assert closed["closed"] is True
    again = close_session(opened["session_id"])
    assert again["closed"] is False
