from __future__ import annotations

from app.emit import emit_schedule
from app.keywords import keyword_object, normalize_keyword
from app.parse import parse_schedule
from app.validate import validate_emitted
from app.apply import apply_operations


def test_fracture_specs_alias() -> None:
    assert normalize_keyword("FRACTURE_WELL") == "FRACTURE_SPECS"
    assert keyword_object("WCONPROD")["fields"][0]["name"] == "well"


def test_emit_block_terminator_and_blank_line() -> None:
    text = """DATES
  1 JAN 2026 /
/
WCONPROD
  'P1' OPEN 100 / 
/
"""
    doc = parse_schedule(text)
    out = emit_schedule(doc)
    assert "WCONPROD" in out
    assert "\n/\n\n" in out or out.rstrip().endswith("/")
    findings = validate_emitted(out, doc)
    assert not [f for f in findings if f.get("code") == "BLOCK_TERMINATOR_MISSING"]


def test_apply_wconprod_and_diff() -> None:
    source = """DATES
  1 JAN 2026 /
/

WCONPROD
  'P1' OPEN 10 /
/
"""
    doc = parse_schedule(source)
    applied, findings = apply_operations(
        doc,
        [{"keyword": "WCONPROD", "operation": "MODIFY", "fields": {"well": "P1", "status": "OPEN", "ORAT": 42}}],
    )
    assert not [f for f in findings if f.get("severity") == "error"]
    out = emit_schedule(applied)
    assert "42" in out


def test_within_date_emit_order_and_weltarg_alias() -> None:
    source = """DATES
  1 JAN 2026 /
/

WCONPROD
  'P1' OPEN 10 /
/

WELSPECS
  'P1' G 1 1 1000 OIL /
/
"""
    out = emit_schedule(parse_schedule(source))
    assert out.index("WELSPECS") < out.index("WCONPROD")
    assert normalize_keyword("WELLTARG") == "WELTARG"
    assert keyword_object("FRACTURE_WELL")["keyword"] == "FRACTURE_SPECS"


def test_build_and_keyword_search_endpoints() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    found = client.get("/keywords/search", params={"intent": "перфорац"})
    assert found.status_code == 200
    names = [item["keyword"] for item in found.json()["keywords"]]
    assert "COMPDATMD" in names
    built = client.post(
        "/build",
        json={
            "units": "METRIC",
            "source_text": "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n",
            "operations": [
                {"keyword": "WCONPROD", "operation": "MODIFY", "fields": {"well": "P1", "status": "OPEN", "ORAT": 7}}
            ],
        },
    )
    assert built.status_code == 200
    body = built.json()
    assert body["ok"] is True
    assert "7" in body["schedule_text"]
    assert "\n/\n" in body["schedule_text"]


def test_commissioning_retarget_moves_first_wconprod() -> None:
    from app.commissioning import run_commissioning_revise

    source = """DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 1* 1* 90 /
/

DATES
  1 MAR 2020 /
/

WCONPROD
  9999 OPEN GRAT 1* 1* 1 /
/
"""
    revised = run_commissioning_revise(source, [{"well": "1601", "date": "23 FEB 2020"}])
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    assert "23 FEB 2020" in text
    feb = text.split("23 FEB 2020")[1]
    assert "1601 OPEN GRAT" in feb
    jan = text.split("1 JAN 2020")[1].split("DATES")[0]
    assert "1601 OPEN GRAT" not in jan


def test_group_rebind_spec_from_task_prose() -> None:
    from app.group_rebind import extract_group_rebind_spec, run_group_rebind_revise

    task = (
        'На основе старого прогнозного schedule - скважины 1601 и 1602 помести в отдельную группу - "DKS", '
        "и задай этим скважинам групповой контроль 200 тыс. м3 газа в сут. (с момента даты ввода этих скважин)."
    )
    source = """GRUPTREE
NORTH FIELD /
/

DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 1* 1* 90 /
  1602 OPEN GRAT 1* 1* 257000 1* 1* 90 /
/
"""
    spec, missing = extract_group_rebind_spec(task, {"1601", "1602", "9999"}, source_text=source)
    assert missing == []
    assert spec["wells"] == ["1601", "1602"] or set(spec["wells"]) == {"1601", "1602"}
    assert spec["parent_group"] == "DKS"
    assert spec["parent_of_parent"] == "FIELD"
    assert spec["control"] == "GRAT"
    assert spec["gas_rate"] == 200000
    assert spec["well_groups"]["1601"] == "G1601"
    revised = run_group_rebind_revise(source, spec)
    assert revised["status"] == "applied"
    assert "DKS FIELD /" in revised["generated_schedule"]
    assert "GCONPROD" in revised["generated_schedule"]


def test_group_intent_ignores_include_filenames() -> None:
    from app.group_rebind import wants_group_rebind

    objective = "На основе старого прогнозного schedule и Excel с новыми датами ввода собрать новый schedule.inc"
    assert wants_group_rebind(objective, {"artifacts": {"schedule_source_5": {"filename": "GRUPTREE.GRDECL"}}}) is False
    group_task = 'скважины 1601 и 1602 помести в отдельную группу - "DKS"'
    assert wants_group_rebind(group_task, {}) is True
    polluted_handoff = "перепривязав скважины в нужные группы согласно baseline"
    assert wants_group_rebind(objective, {"handoff": polluted_handoff}) is False


def test_agent_tools_open_inspect_search_and_operations() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    opened = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t1",
            "objective": "Поставь ORAT 15 на P1",
            "inputs": {"schedule_text": "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n"},
        },
    )
    assert opened.status_code == 200
    body = opened.json()
    assert body["ok"] is True
    sid = body["session_id"]
    assert "P1" in body["inspect"]["wells"]
    search = client.post("/agent-tools/search_keywords", json={"session_id": sid, "intent": "даты ввода"})
    names = [item["keyword"] for item in search.json()["keywords"]]
    assert "DATES" in names
    applied = client.post(
        "/agent-tools/apply_operations",
        json={
            "session_id": sid,
            "operations": {
                "0": {"keyword": "WCONPROD", "operation": "MODIFY", "fields": {"well": "P1", "status": "OPEN", "ORAT": 15}}
            },
        },
    )
    assert applied.status_code == 200
    assert applied.json()["status"] == "completed"
    result = client.get(f"/sessions/{sid}/result")
    assert result.status_code == 200
    assert "15" in result.json()["artifacts"]["schedule_out"]
    invented = client.post(
        "/agent-tools/apply_operations",
        json={
            "session_id": sid,
            "operations": [{"keyword": "WCONPROD", "operation": "MODIFY", "fields": {"well": "NOPE", "ORAT": 1}}],
        },
    )
    assert invented.json()["ok"] is False
    assert invented.json()["error"] == "well_not_in_schedule"


def test_commissioning_facts_prefer_specialist_over_baseline_column() -> None:
    from app.io import commissioning_facts

    context = {
        "data": {
            "excel": {
                "facts": [{"well": "304R", "date": "2019-08-01T00:00:00"}],
                "normalized_rows": [
                    {
                        "preview": [
                            {
                                "Скважина": "304R",
                                "Дата ввода": "2019-08-01T00:00:00",
                                "Дата ввода (baseline в .INC)": "2019-07-01T00:00:00",
                            }
                        ]
                    }
                ],
            }
        }
    }
    rows = commissioning_facts(context, {})
    assert len(rows) == 1
    assert rows[0]["well"] == "304R"
    assert str(rows[0]["date"]).startswith("2019-08-01")



