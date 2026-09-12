"""α2 live gate: --repeat keeps N commissioning rows; demo/recovery count as one."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mas_gate():
    spec = importlib.util.spec_from_file_location("mas_gate", ROOT / "scripts" / "mas_gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _mas_gate()


def test_parse_live_reports_keeps_three_passes_of_the_same_spec(gate) -> None:
    lines = [
        json.dumps({"id": "golden_case_1", "ok": True, "pass": 1, "case_id": "CASE-a", "mismatch_count": 0, "steps": 3}),
        json.dumps({"id": "golden_case_1", "ok": True, "pass": 2, "case_id": "CASE-b", "mismatch_count": 0, "steps": 3}),
        json.dumps({"id": "golden_case_1", "ok": True, "pass": 3, "case_id": "CASE-c", "mismatch_count": 0, "steps": 4}),
        json.dumps({"id": "demo_agent_long_job", "ok": True, "case_id": "CASE-d", "steps": 2}),
    ]
    rows = gate._parse_live_reports("\n".join(lines))
    assert [r["pass"] for r in rows if r["id"] == "golden_case_1"] == [1, 2, 3]
    ok, text = gate._repeat_summary(rows, 3)
    assert ok, text
    assert "golden_case_1" in text and "3/3" in text
    assert "demo_agent_long_job" in text and "1/1" in text


def test_repeat_summary_fails_on_inc_mismatch_or_dropped_pass(gate) -> None:
    rows = [
        {"id": "combat_case0", "ok": True, "pass": 1, "mismatch_count": 0, "steps": 3},
        {"id": "combat_case0", "ok": True, "pass": 2, "mismatch_count": 2, "steps": 3},
        {"id": "rework_round", "ok": True, "mismatch_count": 0, "steps": 3},
    ]
    ok, text = gate._repeat_summary(rows, 3)
    assert not ok
    assert "mismatch" in text
    assert "combat_case0" in text and "2/3" in text
    assert "rework_round" in text and "1/1" in text


def test_expand_jobs_interleaves_full_sets_per_pass() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "simulation-model-example"))
    import run_live_five as five  # noqa: E402

    selected = [{"id": "a"}, {"id": "b"}]
    jobs = five.expand_jobs(selected, 3)
    assert [(j["id"], j["pass"]) for j in jobs] == [
        ("a", 1),
        ("b", 1),
        ("a", 2),
        ("b", 2),
        ("a", 3),
        ("b", 3),
    ]


def test_hitl_commissioning_facts_sends_new_well_table() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "simulation-model-example"))
    import run_live_five as five  # noqa: E402

    spec = {"id": "combat_case3", "policy": "remove", "new_wells": [{"well": "N1", "group": "G"}]}
    gate = {
        "questions": [
            {
                "question_id": "Q-commissioning_facts",
                "question": "В текущей сессии не обнаружены извлечённые пары «скважина — дата ввода» из Excel.",
            }
        ]
    }
    qid, text, _choice = five.hitl_reply(gate, spec=spec)
    assert qid == "Q-commissioning_facts"
    assert "N1" in text
    assert five._gate_asks_for_missing_excel(
        {"questions": [{"question": "Пожалуйста, прикрепите Excel-файл с датами ввода"}]}
    )


def test_provider_busy_message_reads_latest_decision_error() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "simulation-model-example"))
    import run_live_five as five  # noqa: E402

    snap = {
        "status_message": "Оркестратор думает",
        "events": [
            {"kind": "orchestrator.decision", "payload": {"error": "Service unavailable - try again later"}},
        ],
    }
    assert "Service unavailable" in five.provider_busy_message(snap)
    assert five.provider_busy_message({"status_message": "Готово", "events": []}) == ""
