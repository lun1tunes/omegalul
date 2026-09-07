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
    names = [str(row.get("name") or "") for row in keyword_object("WCONPROD")["fields"]]
    assert names[0] in {"well", "WELL"}
    assert keyword_object("WCONPROD")["details"]["kind"] == "schedule_keyword"


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


def test_remove_unlisted_keeps_wildcard_records() -> None:
    """combat_case2 regression: ``WEFAC '*'`` / ``WTEST *`` address all wells — they are not
    «wells outside Excel» and must survive the remove policy untouched."""
    from app.commissioning import run_commissioning_revise

    source = """WEFAC
'*' 0.9500 /
/
WTEST
* 30 P 0 /
/
DATES
  1 JAN 2020 /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 1* 1* 90 /
  201 OPEN GRAT 1* 1* 100000 1* 1* 90 /
/
WEFAC
'P*' 0.9000 /
/
"""
    revised = run_commissioning_revise(
        source,
        [{"well": "1601", "date": "2020-02-01"}],
        unlisted_wells_policy="remove",
    )
    removed = sorted(str(row["well"]) for row in revised["removed"])
    assert removed == ["201"], revised["removed"]
    assert revised["unlisted_wells"] == ["201"]
    text = revised["generated_schedule"]
    assert "'*' 0.9500 /" in text and "* 30 P 0 /" in text and "'P*' 0.9000 /" in text
    assert "201 OPEN" not in text and "1601 OPEN" in text


GROUP_REBIND_SOURCE = """GRUPTREE
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


def test_group_rebind_spec_is_llm_structure_with_explicit_conventions() -> None:
    """The LLM reads «1601 и 1602 → группа "DKS", 200 тыс. м3 газа» and passes a structure.
    No prose regex here; the two rendering conventions are reported as assumptions."""
    from app.group_rebind import gruptree_summary, normalize_group_rebind_spec, run_group_rebind_revise

    baseline = gruptree_summary(GROUP_REBIND_SOURCE)
    assert baseline == {"has_gruptree": True, "groups": ["FIELD", "NORTH"], "roots": ["FIELD"]}
    spec, missing, assumptions = normalize_group_rebind_spec(
        {"wells": "1602, 1601", "parent_group": "dks", "control": "GRAT", "gas_rate": "200 000"},
        baseline=baseline,
    )
    assert missing == []
    assert spec["wells"] == ["1602", "1601"]
    assert spec["parent_group"] == "DKS"
    assert spec["parent_of_parent"] == "FIELD"
    assert spec["gas_rate"] == 200000
    assert spec["well_groups"] == {"1601": "G1601", "1602": "G1602"}
    assert [next(iter(a)) for a in assumptions] == ["well_groups", "parent_of_parent"]
    revised = run_group_rebind_revise(GROUP_REBIND_SOURCE, spec)
    assert revised["status"] == "applied"
    jan = revised["generated_schedule"].split("1 JAN 2020")[1].split("DATES")[0]
    assert "1601 G1601 /" in jan
    assert "DKS FIELD /" in jan
    assert "G1601 DKS /" in jan
    assert "DKS GRAT 2* 200000 /" in jan

    # Nothing is guessed from prose: an empty structure is simply "missing", with no defaults.
    spec, missing, assumptions = normalize_group_rebind_spec({}, baseline={"roots": ["FIELD", "OTHER"]})
    assert missing == ["wells", "parent_group", "parent_of_parent", "control", "gas_rate"]
    assert spec["parent_of_parent"] == "" and assumptions == []
    _, missing, _ = normalize_group_rebind_spec(
        {"wells": ["1601"], "parent_group": "DKS", "parent_of_parent": "FIELD", "control": "GAS", "gas_rate": 0},
    )
    assert missing == ["control", "gas_rate"]


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
    # A malformed tool call is the LLM's problem, not a question for the engineer.
    assert empty.json()["ok"] is False
    assert empty.json()["error"] == "operations_required"
    assert client.get(f"/sessions/{sid}/result").json()["issues"][0]["type"] == "no_apply"
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


def _open(client, objective: str, source: str, **extra):
    opened = client.post(
        "/agent-tools/open_session",
        json={"task_id": "t-grp", "objective": objective, "inputs": {"schedule_text": source}, **extra},
    )
    body = opened.json()
    assert body["ok"] is True
    assert "suggested_capability" not in body, "no regex capability router: the LLM picks the tool"
    return body


def test_group_rebind_incomplete_spec_goes_back_to_llm_not_to_engineer() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    body = _open(client, "скважину P1 помести в отдельную группу", "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n")
    sid = body["session_id"]
    incomplete = client.post("/agent-tools/apply_group_rebind", json={"session_id": sid, "wells": "P1"}).json()
    assert incomplete["ok"] is False
    assert incomplete["error"] == "spec_incomplete"
    assert incomplete["missing"] == ["parent_group", "parent_of_parent", "control", "gas_rate"]
    assert incomplete["baseline"]["has_gruptree"] is False
    assert "GNEW" not in json.dumps(incomplete), "no invented placeholder groups"
    assert set(incomplete["where_to_find"]) == set(incomplete["missing"])
    assert "ask_engineer" in incomplete["message"]
    # Nothing was stored as the agent's answer: the session still has no result for the orchestrator.
    assert client.get(f"/sessions/{sid}/result").json()["issues"][0]["type"] == "no_apply"
    invented = client.post("/agent-tools/apply_group_rebind", json={"session_id": sid, "wells": "P1 P2"}).json()
    assert invented["error"] == "well_not_in_schedule" and invented["wells"] == ["P2"]


def test_group_rebind_full_spec_from_llm_applies_and_summarizes_for_human() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    sid = _open(client, 'скважины 1601 и 1602 → группа "DKS", 200 тыс. м3 газа', GROUP_REBIND_SOURCE)["session_id"]
    applied = client.post(
        "/agent-tools/apply_group_rebind",
        json={"session_id": sid, "spec": {"wells": ["1602", "1601"], "parent_group": "DKS", "control": "GRAT", "gas_rate": 200000}},
    ).json()
    assert applied["status"] == "completed"
    assert applied["message"] == applied["data"]["summary_for_human"]
    assert "Перепривязал 2 скважины (1602, 1601) в группу DKS под FIELD" in applied["message"]
    assert "GRAT 200000 м3/сут" in applied["message"]
    assert {"units": "METRIC"} in applied["assumptions"]
    assert any("parent_of_parent" in a for a in applied["assumptions"])
    text = client.get(f"/sessions/{sid}/result").json()["artifacts"]["schedule_out"]
    jan = text.split("1 JAN 2020")[1].split("DATES")[0]
    assert jan.index("1601 G1601 /") < jan.index("1602 G1602 /"), "baseline order, not LLM order"
    assert "DKS GRAT 2* 200000 /" in jan


def test_ask_engineer_is_the_only_human_question_path_and_must_be_prose() -> None:
    from fastapi.testclient import TestClient

    from app.agent_tools import human_text_problems
    from app.main import app

    client = TestClient(app)
    sid = _open(client, "скважину P1 помести в отдельную группу", "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n")["session_id"]
    machine = client.post(
        "/agent-tools/ask_engineer",
        json={"session_id": sid, "question": "Уточните parent_group для перепривязки групп", "options": ["keep|remove"]},
    ).json()
    assert machine["ok"] is False and machine["error"] == "question_not_human"
    assert any("parent_group" in p for p in machine["problems"])
    assert client.get(f"/sessions/{sid}/result").json()["issues"][0]["type"] == "no_apply"

    asked = client.post(
        "/agent-tools/ask_engineer",
        json={
            "session_id": sid,
            "topic": "target_group",
            "question": "В какую группу поместить скважину P1? В baseline пока нет дерева групп, поэтому нужно её имя и родитель.",
            "options": [{"value": "NEW", "label": "Новая группа — назову ниже"}, "Оставить без группового контроля"],
            "accepts_files": "xlsx",
        },
    ).json()
    assert asked["status"] == "needs_input"
    req = asked["requests"][0]
    assert req["question_id"] == "Q-target_group" and req["type"] == "choice"
    assert req["options"][1] == {"value": "Оставить без группового контроля", "label": "Оставить без группового контроля"}
    assert req["accepts"] == {"free_text": True, "files": ["xlsx"]}
    assert client.get(f"/sessions/{sid}/result").json()["requests"][0]["question"] == req["question"]

    # One result per run: after the question is fixed, the LLM cannot overwrite it with a re-worded
    # ask_engineer or another whole-task apply (live trace 63860: apply → apply → ask → apply → ask).
    again = client.post(
        "/agent-tools/ask_engineer",
        json={"session_id": sid, "question": "А в какую всё-таки группу поместить скважину P1?"},
    ).json()
    assert again["ok"] is False and again["error"] == "result_already_stored"
    assert again["status"] == "needs_input"
    assert "Больше инструменты не вызывай" in again["message"]
    blocked_apply = client.post("/agent-tools/apply_commissioning", json={"session_id": sid}).json()
    assert blocked_apply["error"] == "result_already_stored"
    assert client.get(f"/sessions/{sid}/result").json()["requests"][0]["question"] == req["question"]
    # Read-only tools keep working.
    assert client.post("/agent-tools/inspect_schedule", json={"session_id": sid}).json()["ok"] is True

    assert human_text_problems("В Excel нет скважин: H_304R, 1601. Оставить их запуски как в baseline?") == []
    assert human_text_problems("Уточните rate для перепривязки групп.") == []  # plain words pass; ids don't
    assert human_text_problems("unlisted_wells_policy=keep") != []
    assert human_text_problems('{"decision": "keep"}') != []


def test_engineer_new_well_facts_win_over_orchestrator_echo_of_well_names() -> None:
    """combat_case3 regression (CASE-6a9e4c07): the Decision LLM echoed ``task.new_wells`` as a bare
    list of names; the old lookup took that non-empty list, filtered it to nothing and never looked
    at the engineer's HITL table → the same question was asked again."""
    from app.agent_tools import _new_well_defs

    facts = [{"well": "N001", "date": "2023-01-01", "group": "GNEW", "md_top": 3200, "md_bot": 3240, "control": "GRAT", "rate": 80000}]
    state = {
        "inputs": {"new_wells": ["N001", "N002"]},
        "context": {"hitl": {"answers": {"new_wells_policy": json.dumps({"text": "Параметры в таблице", "new_wells": facts})}}},
    }
    assert _new_well_defs(state) == facts
    # Without an engineer answer, dict rows from inputs are still accepted; bare names are not.
    assert _new_well_defs({"inputs": {"new_wells": ["N001"]}}) == []
    assert _new_well_defs({"inputs": {"new_wells": facts}}) == facts


def test_new_well_facts_come_from_excel_extractor_before_orchestrator_inputs() -> None:
    """Excel Extractor ``extract_well_parameters`` stores the parameters table under
    ``state.data.excel.new_wells``; the engineer attaches a workbook, nobody types JSON."""
    from app.agent_tools import _new_well_defs

    from_excel = [
        {"well": "N001", "date": "2023-01-01", "group": "GNEW", "phase": "OIL", "i": 1, "j": 1, "md_top": 3200, "md_bot": 3240, "control": "GRAT", "rate": 80000},
        {"well": "N002", "Дата ввода": "2023-02-01", "Группа": "GNEW", "MD_TOP": 3210, "MD_BOT": 3250, "Режим": "GRAT", "Дебит": 85000},
    ]
    state = {
        "inputs": {"new_wells": ["N001", "N002"]},
        "context": {"data": {"excel": {"facts": [{"well": "N001", "date": "2023-01-01"}], "new_wells": from_excel}}},
    }
    assert _new_well_defs(state) == from_excel
    # An explicit engineer answer still wins over the extracted table.
    engineer = [{"well": "N001", "group": "GOLD", "md_top": 1, "md_bot": 2, "control": "ORAT", "rate": 1}]
    state["context"]["hitl"] = {"answers": {"new_wells_policy": json.dumps({"text": "Вот таблица", "new_wells": engineer})}}
    assert _new_well_defs(state) == engineer


def test_open_session_shows_engineer_answers_and_rework_to_llm() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    body = client.post(
        "/agent-tools/open_session",
        json={
            "task_id": "t-ans",
            "objective": "сдвинь даты ввода",
            "inputs": {"schedule_text": "DATES\n  1 JAN 2026 /\n/\n", "rework_reason": "Скважина 1602 не сдвинута"},
            "context": {
                "hitl": {
                    "answers": {
                        "unlisted_wells_policy": {"choice": "remove", "label": "Убрать из прогноза", "text": "Убрать из прогноза"},
                        "new_wells_policy": json.dumps({"text": "Траектории приложены", "new_wells": [{"well": "N1"}, {"well": "N2"}]}),
                        "Q-free": "Оставь как есть",
                    }
                }
            },
        },
    ).json()
    assert body["rework_reason"] == "Скважина 1602 не сдвинута"
    answers = {row["question_id"]: row for row in body["engineer_answers"]}
    assert answers["unlisted_wells_policy"]["choice"] == "remove"
    assert answers["unlisted_wells_policy"]["label"] == "Убрать из прогноза"
    assert answers["new_wells_policy"]["attached_facts"] == "new_wells: 2 записей"
    assert "new_wells" not in json.dumps(answers["new_wells_policy"].get("text", "")), "no raw JSON blobs for the LLM"
    assert answers["Q-free"] == {"question_id": "Q-free", "text": "Оставь как есть"}


def test_every_engineer_facing_text_from_tools_is_human() -> None:
    """Every needs_input a Schedule Builder tool can emit: Russian prose, no ids/enums/JSON."""
    from fastapi.testclient import TestClient

    from app.agent_tools import NO_APPLY_QUESTION, human_text_problems
    from app.main import app

    client = TestClient(app)
    texts: list[str] = [NO_APPLY_QUESTION]
    no_source = client.post("/agent-tools/open_session", json={"task_id": "t", "objective": "x", "inputs": {}}).json()
    texts += [no_source["result"]["message"], *(q["question"] for q in no_source["result"]["requests"])]
    sid = _open(client, "сдвинь даты ввода", "DATES\n  1 JAN 2026 /\n/\n\nWCONPROD\n  'P1' OPEN 10 /\n/\n")["session_id"]
    no_facts = client.post("/agent-tools/apply_commissioning", json={"session_id": sid}).json()
    assert no_facts["status"] == "needs_input"
    texts += [no_facts["message"], *(q["question"] for q in no_facts["requests"])]
    for text in texts:
        assert human_text_problems(text) == [], (text, human_text_problems(text))


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
    assert "suggested_capability" not in opened.json()
    assert opened.json()["engineer_answers"] == []
    result = client.get(f"/sessions/{sid}/result")
    assert result.json()["status"] == "needs_input"
    assert result.json()["issues"][0]["type"] == "no_apply"
    assert result.json()["requests"][0]["accepts"]["free_text"] is True
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


def test_group_rebind_emits_wells_in_baseline_order_regardless_of_llm_order() -> None:
    """Deterministic emit: the LLM listed 1602 before 1601 — records still follow the baseline."""
    from app.group_rebind import run_group_rebind_revise
    source = """GRUPTREE
NORTH FIELD /
/

DATES
  1 JAN 2020 /
/

WELSPECS
  1601 NORTH 1 1 1* GAS /
  1602 NORTH 1 1 1* GAS /
/

WCONPROD
  1601 OPEN GRAT 1* 1* 200000 /
  1602 OPEN GRAT 1* 1* 200000 /
/
"""
    spec = {
        "wells": ["1602", "1601", "1602"],
        "parent_group": "DKS",
        "parent_of_parent": "FIELD",
        "well_groups": {"1601": "G1601", "1602": "G1602"},
        "control": "GRAT",
        "gas_rate": 200000,
    }
    text = run_group_rebind_revise(source, spec)["generated_schedule"]
    assert text.index("G1601 DKS /") < text.index("G1602 DKS /")
    assert text.index("1601 G1601 /") < text.index("1602 G1602 /")
    assert text.count("G1602 DKS /") == 1


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
    keep_state = {
        "inputs": {},
        "context": {"hitl": {"answers": {"unlisted_wells_policy": "оставь лишние скважины"}}},
    }
    assert _unlisted_policy(keep_state) == "keep"


def test_unlisted_policy_from_activity_option_button() -> None:
    """Activity stores a clicked option as {choice, text, label}; no regex on the label."""
    from app.agent_tools import _unlisted_policy

    state = {
        "inputs": {},
        "context": {"hitl": {"answers": {
            "unlisted_wells_policy": {"choice": "remove", "text": "Убрать из прогноза", "label": "Убрать из прогноза"},
        }}},
    }
    assert _unlisted_policy(state) == "remove"
    other_gate = {
        "inputs": {},
        "context": {"hitl": {"answers": {"Q-parent-group": {"choice": "remove", "text": "x"}}}},
    }
    assert _unlisted_policy(other_gate) is None


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
    question = next(item for item in revised["questions"] if item.get("id") == "unlisted_wells_policy")
    # Human-facing question: Russian prose + labelled options, no machine enum text.
    assert "expected_format" not in question
    assert [opt["value"] for opt in question["options"]] == ["keep", "remove"]
    assert all(opt["label"] and not opt["label"].isascii() for opt in question["options"])
    assert "keep|remove" not in question["question"]


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


def test_new_well_question_is_human_and_asks_for_facts_not_inc_lines() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [{"well": "1601", "date": "23 FEB 2020"}, {"well": "N001", "date": "1 MAR 2020"}],
        unlisted_wells_policy="keep",
    )
    assert revised["status"] == "needs_input"
    questions = revised["questions"]
    assert len(questions) == 1
    q = questions[0]
    assert q["id"] == "new_wells_policy"
    assert "expected_format" not in q
    assert "N001" in q["question"]
    assert "WELLTRACK" in q["question"] and "GRAT" in q["question"]
    assert "record line" not in q["question"] and "typed" not in q["question"]
    assert q["accepts"]["files"] == ["WELLTRACK .inc", "xlsx"]
    columns = {c["key"] for c in q["accepts"]["table"]["columns"]}
    assert {"well", "group", "md_top", "md_bot", "control", "rate", "welltrack_include"} <= columns
    assert q["wells"] == ["N001"]


def test_compose_new_well_lines_follows_manual_layout() -> None:
    from app.timeline_ops import compose_new_well_lines

    spec = {
        "well": "N001", "date": "2023-01-01", "group": "GNEW", "phase": "OIL", "i": 1, "j": 1,
        "md_top": 3200, "md_bot": 3240, "diameter": 0.15,
        "control": "GRAT", "rate": 80000, "bhp": 90, "vfp_table": 30,
        "welltrack_include": "INCLUDE/WELLTRACK/N001_WELLTRACK.INC",
    }
    out, findings = compose_new_well_lines(spec)
    assert findings == []
    # WELSPECS: WELL GROUP I J REF_DEPTH PHASE
    assert out["welspecs_line"] == " N001 GNEW 1 1 1* OIL /"
    # COMPDATMD: WELL BRANCH MDL MDU DEPTH_TYPE STATUS SAT_TABLE CF DIAMETER
    assert out["compdatmd_line"] == " N001 1* 3200 3240 MD OPEN 2* 0.15 /"
    # WCONPROD: WELL STATUS CONTROL ORAT WRAT GRAT LRAT RESV BHP THP VFP_TABLE
    assert out["wconprod_line"] == " N001 OPEN GRAT 1* 1* 80000 1* 1* 90 1* 30 /"
    assert out["welltrack_include"] == "INCLUDE/WELLTRACK/N001_WELLTRACK.INC"

    # Russian table headers from an xlsx are accepted; trailing defaults are trimmed.
    out_ru, findings_ru = compose_new_well_lines(
        {"Скважина": "N002", "Дата ввода": "2023-02-01", "Группа": "GNEW", "MD_TOP": 3210, "MD_BOT": 3250, "Режим": "GRAT", "Дебит": 85000}
    )
    assert findings_ru == []
    assert out_ru["welspecs_line"] == " N002 GNEW /"
    assert out_ru["wconprod_line"] == " N002 OPEN GRAT 1* 1* 85000 /"

    # Nothing is invented: missing group / control / rate are findings, not defaults.
    _, missing = compose_new_well_lines({"well": "N003", "md_top": 1, "md_bot": 2, "control": "ORAT"})
    codes = {f["code"] for f in missing}
    assert {"NEW_WELL_GROUP_REQUIRED", "NEW_WELL_RATE_REQUIRED"} <= codes


def test_commissioning_new_wells_from_engineering_facts_are_applied() -> None:
    from app.commissioning import run_commissioning_revise

    revised = run_commissioning_revise(
        _TWO_WELL_SOURCE,
        [{"well": "1601", "date": "23 FEB 2020"}, {"well": "N001", "date": "1 MAR 2020"}],
        unlisted_wells_policy="keep",
        new_well_defs=[{
            "well": "N001", "date": "1 MAR 2020", "group": "GNEW", "phase": "GAS",
            "md_top": 1000, "md_bot": 1100, "control": "GRAT", "rate": 50000,
            "welltrack_include": "welltracks/N001.dev",
        }],
    )
    assert revised["status"] == "applied"
    text = revised["generated_schedule"]
    assert "N001 GNEW 1* 1* 1* GAS /" in text
    assert "N001 1* 1000 1100 MD OPEN /" in text
    assert "N001 OPEN GRAT 1* 1* 50000 /" in text
    assert "welltracks/N001.dev" in text


def test_commissioning_summary_describes_actual_changes() -> None:
    """Engineer-facing summary comes from the real diff, not a template count of Excel rows."""
    from app.agent_tools import summarize_commissioning_result

    revised = {
        "status": "applied",
        "moved": [
            {"well": "201", "keyword": "WCONPROD", "to": "01 FEB 2026"},
            {"well": "201", "keyword": "WELSPECS", "to": "01 FEB 2026"},
            {"well": "208", "keyword": "WCONPROD", "to": "01 MAR 2026"},
        ],
        "shifts": [{"well": "201"}, {"well": "208"}, {"well": "212"}],
        "new_wells_applied": [{"well": "N001"}, {"well": "N002"}],
        "removed": [{"well": "301", "keyword": "WCONPROD"}, {"well": "301", "keyword": "WELSPECS"}],
        "unlisted_wells": ["301"],
        "unlisted_wells_policy": "remove",
    }
    summary = summarize_commissioning_result(revised)
    msg = summary["message"]
    assert msg.startswith("Сдвинул даты ввода 2 скважин: 201 → 01 FEB 2026, 208 → 01 MAR 2026.")
    assert "212" in msg and "уже совпадали" in msg
    assert "Добавил 2 скважины (N001, N002)" in msg
    assert "Убрал из прогноза 1 скважину вне Excel: 301." in msg
    assert summary["changed_keywords"] == ["DATES", "WCONPROD", "WELSPECS", "COMPDATMD"]
    assert summary["wells_shifted"] == ["201", "208"]
    assert summary["wells_removed"] == ["301"]
    # no snake_case / machine tokens in the human sentence
    assert "unlisted" not in msg and "=" not in msg and "{" not in msg

    kept = summarize_commissioning_result({
        "status": "applied",
        "moved": [{"well": "201", "keyword": "WCONPROD", "to": "01 FEB 2026"}],
        "shifts": [{"well": "201"}],
        "unlisted_wells": ["301", "302"],
        "unlisted_wells_policy": "keep",
    })
    assert kept["message"] == (
        "Сдвинул даты ввода 1 скважины: 201 → 01 FEB 2026. 2 скважины вне Excel оставил как в baseline: 301, 302."
    )
    assert summarize_commissioning_result({"status": "noop"})["message"].startswith("SCHEDULE без изменений")
