from __future__ import annotations

import json
import re

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
    include = emit_schedule(parse_schedule("INCLUDE\n'../../VFP.INC' /\n/\n"))
    assert include.splitlines()[0] == "INCLUDE"
    assert include.count("../../VFP.INC") == 1
    assert "\n/\n" in include


def test_leading_commented_out_record_stays_off_next_live_line() -> None:
    from app.commissioning import run_commissioning_revise

    source = """DATES
  1 AUG 2025 /
/

GCONPROD
--FIELD GRAT 1* 1* 79018000 6* RATE /
NORTH GRAT 1* 1* 11358904.1 6* RATE /
/

DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
/
"""
    revised = run_commissioning_revise(source, [{"well": "1601", "date": "23 FEB 2020"}])
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    assert "--FIELD GRAT 1* 1* 79018000 6* RATE /" in text
    assert "NORTH GRAT 1* 1* 11358904.1 6* RATE / -- FIELD" not in text
    north = [line for line in text.splitlines() if line.strip().startswith("NORTH GRAT")]
    assert north
    assert "--" not in north[0]


def test_commented_out_row_between_live_records_is_not_glued() -> None:
    from app.emit import emit_schedule
    from app.parse import parse_schedule

    source = """DATES
  1 NOV 2026 /
/

GCONPROD
FIELD GRAT 1* 1* 94821000 6* RATE /
--NORTH GRAT 1* 1* 11400000 6* RATE /
NORTH GRAT 1* 1* 2000000 6* RATE /
/
"""
    text = emit_schedule(parse_schedule(source))
    assert "--NORTH GRAT 1* 1* 11400000 6* RATE /" in text
    field = [line for line in text.splitlines() if line.strip().startswith("FIELD GRAT")]
    assert field
    assert "--" not in field[0]
    live_north = [line for line in text.splitlines() if line.strip().startswith("NORTH GRAT")]
    assert live_north
    assert "--" not in live_north[0]


def test_emit_preserves_slash_before_trailing_token() -> None:
    from app.emit import emit_schedule
    from app.parse import parse_schedule

    source = """BRANPROP
J2C DOUPPG_C 1146 / J11c
/
"""
    text = emit_schedule(parse_schedule(source))
    assert "J2C DOUPPG_C 1146 / J11c" in text
    assert "J2C DOUPPG_C 1146 J11c /" not in text



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
    iso = run_commissioning_revise(source, [{"well": "1601", "date": "2020-02-23T00:00:00"}])
    assert iso["status"] == "applied"
    assert "23 FEB 2020" in iso["generated_schedule"]


def test_commissioning_moves_preamble_welopen_with_first_wconprod() -> None:
    from app.commissioning import run_commissioning_revise

    source = """WELOPEN
304R OPEN /
/

DATES
  1 JAN 2020 /
/

WCONPROD
  304R OPEN GRAT 1* 1* 265900 1* 1* 90 1* 34 /
/

DATES
  1 MAR 2020 /
/

WELOPEN
304R SHUT /
/
"""
    revised = run_commissioning_revise(source, [{"well": "304R", "date": "1 AUG 2019"}])
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    header = text.split("DATES", 1)[0]
    assert "WELOPEN" not in header
    assert "304R OPEN" not in header
    aug = text.split("1 AUG 2019", 1)[1].split("DATES", 1)[0]
    assert "WELOPEN" in aug
    assert "304R OPEN" in aug
    assert "304R OPEN GRAT" in aug
    mar = text.split("1 MAR 2020", 1)[1]
    assert "304R SHUT" in mar


def test_commissioning_preserves_later_wconprod_forecast_controls() -> None:
    from app.commissioning import run_commissioning_revise

    source = """DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 1* 1* 90 /
/

DATES
  1 FEB 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 250000 1* 1* 90 /
/
"""
    revised = run_commissioning_revise(source, [{"well": "1601", "date": "23 FEB 2020"}])
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    feb = text.split("23 FEB 2020")[1]
    assert "1601 OPEN GRAT 1* 1* 200000" in feb
    assert "1601 OPEN GRAT 1* 1* 250000" in text
    assert text.index("250000") < text.index("200000")
    assert revised["control_semantics"]["commissioning_anchor"] == "first WCONPROD per well"


def test_inspect_schedule_exposes_well_object_and_control_history() -> None:
    from app.agent_tools import _compact_inspect

    source = """DATES
  1 JAN 2020 /
/

WELSPECS
  1601 G1 10 20 1000 OIL /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
/

DATES
  1 FEB 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 250000 /
/
"""
    item = _compact_inspect(source)["well_objects"][0]
    assert item["well"] == "1601"
    assert item["identity"]["group"] == "G1"
    assert item["first_wconprod"]["date"] == "1 JAN 2020"
    assert item["forecast_control_count"] == 1


def test_analyze_forecast_controls_distinguishes_overrides_economics_and_policies() -> None:
    from app.agent_tools import _compact_inspect, execute_tool
    from app import sessions

    source = """DATES
  1 JAN 2020 /
/

WELSPECS
  P1 G1 10 20 1000 OIL /
/

WCONHIST
  P1 OPEN 10 /
/

DATES
  1 JAN 2021 /
/

WCONPROD
  P1 OPEN GRAT 1* 1* 200000 /
/

WELTARG
  P1 GRAT 150000 /
/

WECON
  P1 1* 1* 0.95 2* WELL /
/

WTEST
  P1 30 E /
/

WELOPEN
  P1 OPEN /
/

WEFAC
  P1 0.9 /
/

WPIMULT
  P1 1.2 /
/
"""
    state = sessions.put({
        "session_id": sessions.new_session_id(),
        "task_id": "analysis",
        "source_text": source,
        "working_text": source,
        "objective": "",
        "handoff_message": "",
        "inputs": {},
        "context": {},
        "facts": [],
    })
    result = execute_tool(state["session_id"], "analyze_forecast_controls", {"well": "P1"})
    assert result["ok"] is True
    assert result["commissioning_anchor"]["keyword"] == "WCONPROD"
    assert result["control_overrides"][0]["keyword"] == "WELTARG"
    assert result["economic_limits"][0]["keyword"] == "WECON"
    assert result["reopen_policies"][0]["keyword"] == "WTEST"
    assert result["efficiency_events"][0]["keyword"] == "WEFAC"
    assert result["connection_multipliers"][0]["keyword"] == "WPIMULT"
    assert result["needs_input"] == []


def test_generic_operations_protect_factual_control_and_require_weltarg_base() -> None:
    from app.agent_tools import execute_tool
    from app import sessions

    source = """DATES
  1 JAN 2020 /
/

WCONPROD
  P1 OPEN GRAT 1* 1* 120000 / -- ФАКТ
/
"""
    state = sessions.put({
        "session_id": sessions.new_session_id(),
        "task_id": "semantic",
        "source_text": source,
        "working_text": source,
        "objective": "",
        "handoff_message": "",
        "inputs": {},
        "context": {},
        "facts": [],
    })
    factual = execute_tool(state["session_id"], "apply_operations", {
        "operations": [{"keyword": "WCONPROD", "operation": "MODIFY", "fields": {"well": "P1", "ORAT": 1}}],
    })
    assert factual["status"] == "failed"
    assert factual["issues"][0]["code"] == "FACTUAL_WCONPROD_PROTECTED"

    no_base_source = "DATES\n  1 JAN 2020 /\n/\n\nWELSPECS\n  P1 G1 1 1 1000 OIL /\n/\n"
    no_base_state = sessions.put({
        **state,
        "session_id": sessions.new_session_id(),
        "source_text": no_base_source,
        "working_text": no_base_source,
    })
    missing = execute_tool(no_base_state["session_id"], "apply_operations", {
        "operations": [{"keyword": "WELTARG", "operation": "ADD", "fields": {"well": "P1", "quantity": "BHP", "value": 100}}],
    })
    assert missing["status"] == "failed"
    assert missing["issues"][0]["code"] == "WELTARG_BASE_CONTROL_MISSING"


def test_fact_comment_is_preserved_and_factual_wconprod_is_not_retargeted() -> None:
    from app.commissioning import run_commissioning_revise
    from app.parse import parse_schedule
    from app.well_model import build_well_objects

    source = """DATES
  1 JAN 2019 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 120000 / -- ФАКТ: история
/

DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 / -- forecast start
/

DATES
  1 FEB 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 250000 / -- forecast mode
/
"""
    objects = build_well_objects(parse_schedule(source))
    item = objects[0]
    assert item["factual_control_events"][0]["factual"] is True
    assert item["commissioning_wconprod"]["tokens"][-1] == "200000"

    revised = run_commissioning_revise(source, [{"well": "1601", "date": "23 FEB 2020"}])
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    assert "120000 / -- ФАКТ: история" in text
    assert "200000 / -- forecast start" in text
    assert "250000 / -- forecast mode" in text
    assert "23 FEB 2020" in text
    assert text.index("120000 / -- ФАКТ: история") < text.index("23 FEB 2020")
    assert text.index("120000") < text.index("200000")


def test_remove_cannot_delete_factual_wconprod() -> None:
    from app.apply import apply_operations
    from app.parse import parse_schedule

    source = """DATES
  1 JAN 2019 /
/

WCONPROD
  1601 OPEN GRAT 120000 / -- факт
/
"""
    _, findings = apply_operations(
        parse_schedule(source),
        [{"keyword": "WCONPROD", "operation": "REMOVE", "fields": {"well": "1601"}}],
    )
    assert any(item["code"] == "FACTUAL_WCONPROD_PROTECTED" for item in findings)


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
    text = revised["generated_schedule"]
    jan = text.split("1 JAN 2020")[1].split("DATES")[0]
    assert "1601 G1601 /" in jan
    assert "DKS FIELD /" in jan
    assert "G1601 DKS /" in jan
    assert "DKS GRAT 2* 200000 /" in jan


def test_load_source_fetches_from_activity_artifact(monkeypatch) -> None:
    from app.io import load_source

    class _Resp:
        def read(self):
            return b"DATES\n/"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    seen: dict[str, str] = {}

    def fake_urlopen(url, timeout=30):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("app.io.urlopen", fake_urlopen)
    text = load_source(
        {
            "activity_base_url": "http://mas-activity:8200",
            "artifacts": {"schedule_source": {"filename": "base.inc", "artifact_id": "schedule_source"}},
        },
        "CASE-1",
    )
    assert seen["url"] == "http://mas-activity:8200/cases/CASE-1/artifacts/schedule_source"
    assert "DATES" in text
    seen.clear()
    text = load_source(
        {
            "activity_base_url": "http://mas-activity:8200",
            "schedule_root": "MONITORING_FDP.INC",
            "artifacts": {
                "schedule_source": {"filename": "GRUPTREE.GRDECL", "artifact_id": "schedule_source"},
                "schedule_source_1": {"filename": "MONITORING_FDP.INC", "artifact_id": "schedule_source_1"},
            },
        },
        "CASE-1",
    )
    assert seen["url"] == "http://mas-activity:8200/cases/CASE-1/artifacts/schedule_source_1"
    assert "DATES" in text
    from app.group_rebind import wants_group_rebind

    objective = "На основе старого прогнозного schedule и Excel с новыми датами ввода собрать новый schedule.inc"
    assert wants_group_rebind(objective, {"artifacts": {"schedule_source_5": {"filename": "GRUPTREE.GRDECL"}}}) is False
    group_task = 'скважины 1601 и 1602 помести в отдельную группу - "DKS"'
    assert wants_group_rebind(group_task, {}) is True
    polluted_handoff = "перепривязав скважины в нужные группы согласно baseline"
    assert wants_group_rebind(objective, {"handoff": polluted_handoff}) is False


def test_bind_case_packet_hydrates_nested_artifacts_and_facts(monkeypatch) -> None:
    from app.io import bind_case_packet, commissioning_facts, load_source

    packet = {
        "state": {
            "schedule_root": "base.inc",
            "artifacts": {
                "excel": {"filename": "a.xlsx", "artifact_id": "excel"},
                "schedule": {
                    "source": {"filename": "base.inc", "artifact_id": "schedule_source"},
                    "grdecl": [{"filename": "G.GRDECL", "artifact_id": "schedule_source_1"}],
                    "includes": [{"filename": "VFP.INC", "artifact_id": "schedule_source_2"}],
                },
            },
            "data": {"excel": {"facts": [{"well": "P1", "date": "1 JAN 2020"}]}},
        }
    }

    class _Resp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=15):
        if "/state" in str(url):
            return _Resp(json.dumps(packet).encode())
        return _Resp(b"DATES\n/")

    monkeypatch.setattr("app.io.urlopen", fake_urlopen)
    inputs, context = bind_case_packet(
        {"activity_base_url": "http://mas-activity:8200", "artifact_ids": ["excel", "schedule_source"]},
        {"hitl": {"pending": False}},
        "CASE-1",
    )
    assert inputs["artifacts"]["schedule_source"]["filename"] == "base.inc"
    assert inputs["artifacts"]["schedule_source_1"]["filename"] == "G.GRDECL"
    assert inputs["schedule_root"] == "base.inc"
    assert context["excel"]["facts"][0]["well"] == "P1"
    assert commissioning_facts(context, inputs)[0]["well"] == "P1"
    assert "DATES" in load_source(inputs, "CASE-1", "http://mas-activity:8200")


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


def test_apply_operations_accepts_array_and_rejects_empty() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    opened = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-ops",
            "objective": "Поставь ORAT 20 на P1",
            "inputs": {"schedule_text": "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n"},
        },
    )
    sid = opened.json()["session_id"]
    empty = client.post("/agent-tools/apply_operations", json={"session_id": sid, "operations": {}})
    assert empty.json()["status"] == "needs_input"
    applied = client.post(
        "/agent-tools/apply_operations",
        json={
            "session_id": sid,
            "operations": [
                {"keyword": "WCONPROD", "operation": "MODIFY", "fields": {"well": "P1", "status": "OPEN", "ORAT": 20}}
            ],
        },
    )
    assert applied.json()["status"] == "completed"
    result = client.get(f"/sessions/{sid}/result")
    assert "20" in result.json()["artifacts"]["schedule_out"]


def test_group_rebind_asks_parent_group_when_no_gruptree() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    opened = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-grp",
            "objective": "скважину P1 помести в отдельную группу",
            "inputs": {"schedule_text": "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n"},
        },
    )
    body = opened.json()
    assert body["suggested_capability"] == "group_rebind"
    sid = body["session_id"]
    applied = client.post("/agent-tools/apply_group_rebind", json={"session_id": sid})
    payload = applied.json()
    assert payload["status"] == "needs_input"
    assert payload["requests"][0]["question_id"] == "Q-parent-group"
    assert "GRUPTREE" in payload["requests"][0]["question"]
    assert "GNEW" in payload["requests"][0]["options"]


def test_session_result_needs_input_without_apply_then_close() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    opened = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-idle",
            "objective": "посмотри schedule",
            "inputs": {"schedule_text": "DATES\n  1 JAN 2026 /\n/\n"},
        },
    )
    sid = opened.json()["session_id"]
    assert opened.json()["suggested_capability"] == "operations"
    result = client.get(f"/sessions/{sid}/result")
    assert result.json()["status"] == "needs_input"
    assert result.json()["issues"][0]["type"] == "no_apply"
    closed = client.post(f"/sessions/{sid}/close")
    assert closed.json()["ok"] is True
    assert client.get(f"/sessions/{sid}/result").status_code == 404


def test_session_result_autobuilds_dirty_working_text() -> None:
    from app import sessions
    from app.agent_tools import open_session, session_result

    opened = open_session(
        {
            "task_id": "t-build",
            "objective": "сборка",
            "inputs": {"schedule_text": "DATES\n  1 JAN 2026 /\n/\n"},
        }
    )
    sid = opened["session_id"]
    state = sessions.get(sid)
    state["working_text"] = "DATES\n  2 JAN 2026 /\n/\n"
    sessions.save(state)
    result = session_result(sid)
    assert result["status"] in {"completed", "failed"}
    assert "2 JAN 2026" in result["artifacts"]["schedule_out"]


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


def test_commissioning_runs_without_node() -> None:
    from app.commissioning import run_commissioning_revise
    source = """DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
/
"""
    revised = run_commissioning_revise(source, [{"well": "1601", "date": "23 FEB 2020"}])
    assert revised["status"] == "applied"
    assert "23 FEB 2020" in revised["generated_schedule"]


def test_group_rebind_runs_without_node() -> None:
    from app.group_rebind import run_group_rebind_revise
    source = """GRUPTREE
NORTH FIELD /
/

DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
/
"""
    revised = run_group_rebind_revise(
        source,
        {
            "wells": ["1601"],
            "parent_group": "DKS",
            "parent_of_parent": "FIELD",
            "well_groups": {"1601": "G1601"},
            "control": "GRAT",
            "gas_rate": 200000,
        },
    )
    assert revised["status"] == "applied"
    assert "DKS FIELD /" in revised["generated_schedule"]


def test_hitl_json_answers_yield_policy_and_new_well_defs() -> None:
    import json

    from app.agent_tools import _new_well_defs, _unlisted_policy

    defs = [{"well": "N001", "welspecs_line": " N001 GNEW 1 1 1* OIL /"}]
    state = {
        "inputs": {},
        "context": {
            "hitl": {
                "answers": {
                    "new_wells_policy": json.dumps(
                        {"unlisted_wells_policy": "remove", "new_well_defs": defs},
                        ensure_ascii=False,
                    )
                }
            }
        },
    }
    assert _unlisted_policy(state) == "remove"
    assert _new_well_defs(state)[0]["well"] == "N001"


def test_unlisted_policy_from_plain_hitl_string() -> None:
    from app.agent_tools import _unlisted_policy

    state = {
        "inputs": {},
        "context": {"hitl": {"answers": {"unlisted_wells_policy": "unlisted_wells_policy=remove"}}},
    }
    assert _unlisted_policy(state) == "remove"


def test_wefac_wildcard_is_not_an_unlisted_well() -> None:
    from app.commissioning import run_commissioning_revise

    source = """WEFAC
'*' 0.9500 /
/

DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
  201 OPEN GRAT 1* 1* 100000 /
/
"""
    revised = run_commissioning_revise(
        source,
        [{"well": "1601", "date": "23 FEB 2020"}],
        instruction_blob=_PROSE_REMOVE,
    )
    assert "*" not in (revised.get("unlisted_wells") or [])
    assert "201" in revised["unlisted_wells"]


_PROSE_REMOVE = (
    "REVISE прогнозный SCHEDULE по Excel с датами ввода. В Excel не все скважины: "
    "тех скважин, которые есть в примере schedule но нет в файле с запусками — убрать."
)
_PROSE_KEEP = (
    "REVISE прогнозный SCHEDULE: сдвинуть даты ввода по Excel. "
    "Инструкция молчит про скважины, которых нет в Excel — по умолчанию сохранить их запуски."
)
_TWO_WELL_SOURCE = """DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
  201 OPEN GRAT 1* 1* 100000 /
/

WELOPEN
  1601 OPEN /
  201 OPEN /
/
"""


def test_detect_unlisted_policy_from_prose() -> None:
    from app.timeline_ops import detect_unlisted_wells_policy

    assert detect_unlisted_wells_policy(_PROSE_REMOVE) == "remove"
    assert detect_unlisted_wells_policy(_PROSE_KEEP) == "keep"
    assert detect_unlisted_wells_policy(
        "Во вложении excel файл с новыми датами ввода скважин."
    ) == "keep"


def test_commissioning_prose_remove_without_enum_needs_unlisted_policy() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [{"well": "1601", "date": "23 FEB 2020"}],
        instruction_blob=_PROSE_REMOVE,
    )
    assert revised["status"] == "needs_input"
    assert revised["generated_schedule"] == ""
    assert revised["unlisted_wells_policy"] is None
    assert "201" in revised["unlisted_wells"]
    assert any(item["code"] == "UNLISTED_WELLS_POLICY_REQUIRED" for item in revised["findings"])
    assert any(item.get("id") == "unlisted_wells_policy" for item in revised["questions"])


def test_commissioning_explicit_remove_drops_unlisted() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [{"well": "1601", "date": "23 FEB 2020"}],
        instruction_blob=_PROSE_REMOVE,
        unlisted_wells_policy="remove",
    )
    assert revised["status"] == "applied"
    assert revised["unlisted_wells_policy"] == "remove"
    text = revised["generated_schedule"]
    assert "23 FEB 2020" in text
    assert "1601 OPEN GRAT" in text
    assert not re.search(r"\b201\b", text)
    assert any(item["code"] == "UNLISTED_WELLS_REMOVED" for item in revised["findings"])


def test_commissioning_keep_preserves_unlisted() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [{"well": "1601", "date": "23 FEB 2020"}],
        instruction_blob=_PROSE_KEEP,
    )
    assert revised["status"] == "applied"
    assert revised["unlisted_wells_policy"] == "keep"
    text = revised["generated_schedule"]
    assert re.search(r"\b201\b", text)
    assert "201 OPEN GRAT" in text
    assert any(item["code"] == "UNLISTED_WELLS_KEPT" for item in revised["findings"])


def test_commissioning_new_well_without_defs_needs_hitl() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [
            {"well": "1601", "date": "23 FEB 2020"},
            {"well": "N001", "date": "1 MAR 2020"},
        ],
        unlisted_wells_policy="keep",
    )
    assert revised["status"] == "needs_input"
    assert revised["generated_schedule"] == ""
    assert "N001" in revised["new_wells"]
    assert any(item["code"] == "NEW_WELLS_REQUIRE_HITL" for item in revised["findings"])
    assert any(item.get("id") == "new_wells_policy" for item in revised["questions"])


def test_commissioning_new_well_defs_are_applied() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [
            {"well": "1601", "date": "23 FEB 2020"},
            {"well": "N001", "date": "1 MAR 2020"},
        ],
        unlisted_wells_policy="keep",
        new_well_defs=[{
            "well": "N001",
            "date": "1 MAR 2020",
            "welltrack_include": "welltracks/N001.dev",
            "welspecs_line": "N001 GNEW 1 1 1* GAS /",
            "compdatmd_lines": ["N001 1 1 1 1 OPEN 1* 1* 1* 1* 1* 1* 1000 1100 /"],
            "wconprod_line": "N001 OPEN GRAT 1* 1* 50000 /",
        }],
    )
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    assert "INCLUDE" in text
    assert "welltracks/N001.dev" in text
    assert "N001 GNEW" in text
    assert "1 MAR 2020" in text
    assert "N001 OPEN GRAT 1* 1* 50000" in text
    assert any(item["code"] == "NEW_WELLS_APPLIED" for item in revised["findings"])
    assert "N001" in revised["new_wells"]
    assert "N001" in (revised.get("new_wells_applied") or [{}])[0].get("well", "")
