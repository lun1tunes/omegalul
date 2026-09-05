from __future__ import annotations

from app.state_shape import (
    compact_decision_context,
    decode_hitl_answer,
    flatten_artifacts,
    nest_artifacts,
    slim_excel_data,
)


def test_nest_flatten_roundtrip_numbered_includes() -> None:
    flat = {
        "excel": {"filename": "a.xlsx", "artifact_id": "excel"},
        "schedule_source": {"filename": "base.inc", "artifact_id": "schedule_source"},
        "schedule_source_1": {"filename": "G.GRDECL", "artifact_id": "schedule_source_1"},
        "schedule_source_2": {"filename": "VFP.INC", "artifact_id": "schedule_source_2"},
    }
    nested = nest_artifacts(flat)
    assert nested["excel"]["filename"] == "a.xlsx"
    assert nested["schedule"]["source"]["filename"] == "base.inc"
    assert [item["filename"] for item in nested["schedule"]["grdecl"]] == ["G.GRDECL"]
    assert [item["filename"] for item in nested["schedule"]["includes"]] == ["VFP.INC"]
    assert "schedule_source_1" not in nested
    back = flatten_artifacts(nested)
    assert back["schedule_source"]["filename"] == "base.inc"
    assert back["schedule_source_1"]["filename"] == "G.GRDECL"
    assert back["schedule_source_2"]["filename"] == "VFP.INC"


def test_decode_hitl_answer_parses_nested_json_string() -> None:
    assert decode_hitl_answer("январь") == "январь"
    assert decode_hitl_answer('{"text":"январь"}') == {"text": "январь"}
    assert decode_hitl_answer('"{\\"text\\":\\"ok\\"}"') == {"text": "ok"}


def test_slim_excel_keeps_fact_count_and_caps_preview() -> None:
    slim = slim_excel_data(
        {
            "facts": [{"well": "A", "date": "2020-01-01", "values": {"x": 1}}],
            "normalized_rows": [
                {
                    "table_id": "t1",
                    "columns": ["well"],
                    "preview": [{"well": "A"}, {"well": "B"}, {"well": "C"}, {"well": "D"}],
                    "row_count": 14,
                }
            ],
        }
    )
    assert slim["facts"] == [{"well": "A", "date": "2020-01-01"}]
    assert slim["normalized_rows"][0]["preview_count"] == 14
    assert len(slim["normalized_rows"][0]["preview"]) == 3


def test_compact_does_not_copy_facts_or_current_task_body() -> None:
    ctx = compact_decision_context(
        {
            "goal": "даты",
            "version": 4,
            "artifacts": {
                "excel": {"filename": "a.xlsx", "artifact_id": "excel"},
                "schedule_source": {"filename": "base.inc", "artifact_id": "schedule_source"},
                "schedule_source_1": {"filename": "G.GRDECL", "artifact_id": "schedule_source_1"},
            },
            "data": {
                "facts": [{"well": "dup"}],
                "excel": {"facts": [{"well": "A", "date": "2020-01-01", "values": {"row": True}}]},
            },
            "current_task": {
                "task_id": "TASK-1",
                "agent_id": "excel_extractor",
                "context": {"data": {"facts": [{"well": "dup"}]}},
            },
            "hitl": {"pending": False, "answers": {"Q-1": '{"text":"ok"}'}},
        }
    )
    assert ctx["files"]["excel"] == 1
    assert ctx["files"]["grdecl"] == 1
    assert ctx["files"]["includes"] == 0
    assert ctx["excel_facts"] == 1
    assert ctx["excel_filename"] == "a.xlsx"
    assert ctx["wells_in_excel"] == ["A"]
    assert ctx["current_task"] == {"task_id": "TASK-1", "agent_id": "excel_extractor"}
    assert ctx["hitl_answer_ids"] == ["Q-1"]
    assert ctx["unlisted_wells_policy"] is None
    assert ctx["version"] == 4


def test_compact_reads_unlisted_policy_from_hitl_answer() -> None:
    ctx = compact_decision_context(
        {
            "hitl": {
                "pending": False,
                "answers": {"unlisted_wells_policy": "unlisted_wells_policy=remove"},
            }
        }
    )
    assert ctx["unlisted_wells_policy"] == "remove"
    assert ctx["hitl_answer_ids"] == ["unlisted_wells_policy"]
    keep = compact_decision_context(
        {
            "hitl": {
                "pending": False,
                "answers": {"unlisted_wells_policy": "оставь лишние скважины"},
            }
        }
    )
    assert keep["unlisted_wells_policy"] == "keep"
