"""6.0: named dataset → schema keyword. No INTENT_ALIASES, no column→keyword maps."""

from __future__ import annotations

import app.keywords as keywords
from app.apply import apply_mapped_records, tokens_from_schema
from app.emit import emit_schedule
from app.parse import parse_schedule
from mas_agent_kit import dataset_entry


def _schedule() -> str:
    return (
        "DATES\n  1 JAN 2026 /\n/\n\n"
        "WCONPROD\n  'P1' OPEN 10 /\n  'P2' OPEN 10 /\n/\n\n"
        "WEFAC\n  'P1' 1 /\n  'P2' 1 /\n/\n\n"
        "DATES\n  1 FEB 2026 /\n/\n\n"
        "WEFAC\n  'P1' 1 /\n  'P2' 1 /\n/\n"
    )


def test_search_keywords_is_catalog_lexical_not_aliases() -> None:
    assert not hasattr(keywords, "INTENT_ALIASES")
    names = [item["keyword"] for item in keywords.search_keywords("коэффициент эксплуатации")]
    assert names[0] == "WEFAC"
    dates = [item["keyword"] for item in keywords.search_keywords("даты расчётных периодов")]
    assert "DATES" in dates
    # Old alias «дат» must not inject WCONPROD.
    stem = {item["keyword"] for item in keywords.search_keywords("дат")}
    assert "WCONPROD" not in stem


def test_tokens_from_schema_follow_catalogue_positions() -> None:
    tokens = tokens_from_schema("WEFAC", {"WELL": "P1", "FACTOR": 0.81})
    assert tokens[0].strip("'\"") == "P1"
    assert tokens[1] in {"0.81", ".81"}


def test_apply_mapped_records_updates_every_existing_well() -> None:
    doc, findings = apply_mapped_records(
        parse_schedule(_schedule()),
        "WEFAC",
        [{"WELL": "P1", "FACTOR": 0.81}, {"WELL": "P2", "FACTOR": 0.73}],
    )
    assert not findings
    text = emit_schedule(doc)
    wefac = [line.strip() for line in text.splitlines() if line.strip().startswith("'P1'") or line.strip().startswith("P1")]
    factors = [tok for line in text.splitlines() if "P1" in line and "OPEN" not in line for tok in line.split()]
    assert "0.81" in text
    assert "0.73" in text
    assert text.count("0.81") == 2
    assert text.count("0.73") == 2
    assert wefac or factors


def test_open_session_exposes_datasets_and_apply_dataset(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    entry = dataset_entry(
        "uptime",
        [{"name": "well", "type": "text"}, {"name": "factor", "type": "number"}],
        [{"well": "P1", "factor": 0.81}, {"well": "P2", "factor": 0.73}],
        title="Коэффициенты эксплуатации",
    )
    opened = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-ds",
            "objective": "Проставь коэффициенты эксплуатации",
            "inputs": {"schedule_text": _schedule(), "datasets": {"uptime": entry}},
        },
    ).json()
    assert opened["ok"] is True
    assert opened["dataset_count"] == 1
    assert opened["datasets"][0]["name"] == "uptime"
    assert opened["datasets"][0]["fields"] == ["well", "factor"]
    sid = opened["session_id"]
    inspected = client.post("/agent-tools/inspect_dataset", json={"session_id": sid, "name": "uptime"}).json()
    assert inspected["row_count"] == 2
    applied = client.post(
        "/agent-tools/apply_dataset",
        json={
            "session_id": sid,
            "dataset": "uptime",
            "keyword": "WEFAC",
            "field_map": {"well": "WELL", "factor": "FACTOR"},
        },
    ).json()
    assert applied.get("status") == "completed"
    assert "WEFAC" in applied.get("data", {}).get("changed_keywords", [])
    text = client.get(f"/sessions/{sid}/result").json()["artifacts"]["schedule_out"]
    assert text.count("0.81") == 2
    assert text.count("0.73") == 2


def test_apply_dataset_rejects_unknown_well_and_empty_values() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    sid = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-bad",
            "objective": "Проставь коэффициенты",
            "inputs": {
                "schedule_text": _schedule(),
                "datasets": {
                    "uptime": dataset_entry(
                        "uptime",
                        [{"name": "well"}, {"name": "factor"}],
                        [{"well": "NOPE", "factor": 0.5}],
                    )
                },
            },
        },
    ).json()["session_id"]
    invented = client.post(
        "/agent-tools/apply_dataset",
        json={"session_id": sid, "dataset": "uptime", "keyword": "WEFAC", "field_map": {"well": "WELL", "factor": "FACTOR"}},
    ).json()
    assert invented["ok"] is False
    assert invented["code"] == "well_not_in_schedule"

    sid2 = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-empty",
            "objective": "Проставь коэффициенты",
            "inputs": {
                "schedule_text": _schedule(),
                "datasets": {
                    "uptime": dataset_entry(
                        "uptime",
                        [{"name": "well"}, {"name": "factor"}],
                        [{"well": "P1", "factor": ""}],
                    )
                },
            },
        },
    ).json()["session_id"]
    hole = client.post(
        "/agent-tools/apply_dataset",
        json={"session_id": sid2, "dataset": "uptime", "keyword": "WEFAC", "field_map": {"well": "WELL", "factor": "FACTOR"}},
    ).json()
    assert hole["ok"] is False
    assert hole["code"] == "dataset_values_missing"
    assert "error" not in hole


def test_apply_dataset_unknown_keyword_and_incomplete_map() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    sid = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-map",
            "objective": "Проставь коэффициенты",
            "inputs": {
                "schedule_text": _schedule(),
                "datasets": {
                    "uptime": dataset_entry("uptime", [{"name": "well"}, {"name": "factor"}], [{"well": "P1", "factor": 0.8}])
                },
            },
        },
    ).json()["session_id"]
    fake = client.post(
        "/agent-tools/apply_dataset",
        json={"session_id": sid, "dataset": "uptime", "keyword": "NOTAKEY", "field_map": {"well": "WELL"}},
    ).json()
    assert fake["ok"] is False
    assert fake["code"] == "unknown_keyword"
    incomplete = client.post(
        "/agent-tools/apply_dataset",
        json={"session_id": sid, "dataset": "uptime", "keyword": "WEFAC", "field_map": {"well": "WELL"}},
    ).json()
    assert incomplete["ok"] is False
    assert incomplete["code"] == "spec_incomplete"
    assert "FACTOR" in (incomplete.get("missing") or [])
