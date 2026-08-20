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
    assert facts == [{"well": "1601", "date": "23 FEB 2020", "values": {"Скважина": "1601", "Дата ввода": "23 FEB 2020"}}]


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
