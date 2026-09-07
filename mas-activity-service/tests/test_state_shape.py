from __future__ import annotations

from app.state_shape import (
    artifact_cards,
    artifact_filenames,
    artifact_text,
    artifacts_from_indexed,
    compact_decision_context,
    compact_unlisted_policy,
    decode_hitl_answer,
    deliverables,
    flatten_artifacts,
    is_unlisted_wells_gate,
    nest_artifacts,
    parse_keep_remove,
    slim_excel_data,
)


def test_artifact_cards_roundtrip_three_kinds_two_producers() -> None:
    """Phase 1.5: cards {kind, producer} survive nest/flatten; old ids stay; text never leaks into cards."""
    flat = {
        "excel": {"artifact_id": "excel", "filename": "dates.xlsx", "role": "excel", "kind": "input", "producer": "user"},
        "excel_1": {"artifact_id": "excel_1", "filename": "params.xlsx", "role": "excel", "kind": "input", "producer": "user"},
        "schedule_source": {"artifact_id": "schedule_source", "filename": "base.inc", "role": "schedule_source", "kind": "input", "producer": "user"},
        "wells_table": {"artifact_id": "wells_table", "filename": "wells.csv", "role": "attachment", "kind": "intermediate", "producer": "excel_extractor"},
        "schedule_out": {"artifact_id": "schedule_out", "role": "schedule_out", "kind": "deliverable", "producer": "schedule_builder", "text": "DATES\n/\n"},
        "diff": {"artifact_id": "diff", "role": "diff", "kind": "deliverable", "producer": "schedule_builder", "text": "--- a\n+++ b\n"},
    }
    nested = nest_artifacts(flat)
    assert nested["schedule"]["diff"]["producer"] == "schedule_builder"
    assert [a["artifact_id"] for a in nested["attachments"]] == ["excel_1", "wells_table"]
    back = flatten_artifacts(nested)
    assert set(back) == set(flat)
    assert nest_artifacts(back) == nested

    cards = artifact_cards(nested)
    assert sorted((c["artifact_id"], c["kind"], c["producer"]) for c in cards) == sorted(
        [
            ("excel", "input", "user"),
            ("excel_1", "input", "user"),
            ("schedule_source", "input", "user"),
            ("wells_table", "intermediate", "excel_extractor"),
            ("schedule_out", "deliverable", "schedule_builder"),
            ("diff", "deliverable", "schedule_builder"),
        ]
    )
    # Inputs first, then what agents produced.
    assert [c["kind"] for c in cards[:3]] == ["input"] * 3
    assert {c["kind"] for c in cards[3:]} == {"intermediate", "deliverable"}
    assert all("text" not in c for c in cards)
    assert [d["filename"] for d in deliverables(nested)] == ["schedule_result.inc", "schedule_changes.diff"]
    assert deliverables(nested)[0]["bytes"] == len("DATES\n/\n".encode("utf-8"))
    assert artifact_text(nested, "diff") == "--- a\n+++ b\n"
    assert artifact_text(nested, "excel") is None
    # Inputs are what the case started from; agent outputs are not "attached files".
    assert sorted(artifact_filenames(nested)) == ["base.inc", "dates.xlsx", "params.xlsx"]


def test_legacy_state_defaults_kind_and_producer() -> None:
    """Old states: loose ``schedule_out``/``diff`` strings are deliverables (producer unknown), uploads are inputs."""
    legacy = {"excel": {"filename": "a.xlsx", "artifact_id": "excel"}, "schedule_out": "x" * 30, "schedule": {"diff": "d"}}
    cards = {c["artifact_id"]: c for c in artifact_cards(legacy)}
    assert (cards["excel"]["kind"], cards["excel"]["producer"]) == ("input", "user")
    assert (cards["schedule_out"]["kind"], cards["schedule_out"]["producer"]) == ("deliverable", "")
    assert cards["diff"]["kind"] == "deliverable"
    assert artifact_cards(legacy, producers={"schedule_out": "schedule_builder"})[1]["producer"] in {"schedule_builder", ""}
    assert {c["producer"] for c in deliverables(legacy, producers={"schedule_out": "schedule_builder", "diff": "schedule_builder"})} == {"schedule_builder"}
    # An empty diff is not an artifact.
    assert "diff" not in flatten_artifacts({"schedule": {"diff": ""}})
    compact = compact_decision_context({"artifacts": legacy})
    assert "has_schedule_out" not in compact
    assert [d["artifact_id"] for d in compact["deliverables"]] == ["schedule_out", "diff"] or sorted(d["artifact_id"] for d in compact["deliverables"]) == ["diff", "schedule_out"]


def test_uploads_become_input_cards() -> None:
    nested = artifacts_from_indexed({"excel": ("a.xlsx", b"12", "application/vnd.ms-excel")}, created_at="2026-09-07T00:00:00+00:00")
    card = artifact_cards(nested)[0]
    assert (card["kind"], card["producer"], card["bytes"], card["created_at"]) == ("input", "user", 2, "2026-09-07T00:00:00+00:00")


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


def test_unlisted_policy_uses_word_boundaries_like_n8n() -> None:
    assert parse_keep_remove("upkeep of extra wells", keyed=True) == ""
    assert parse_keep_remove("housekeeper removed extras", keyed=True) == ""
    assert parse_keep_remove("keep extra wells", keyed=True) == "keep"
    assert parse_keep_remove("remove extras", keyed=True) == "remove"
    assert parse_keep_remove("keep extra wells", keyed=False) == ""
    assert parse_keep_remove("оставь лишние скважины") == "keep"
    assert compact_unlisted_policy({"unlisted_wells_policy": "upkeep of extra wells"}) is None
    assert compact_unlisted_policy({"unlisted_wells_policy": "keep extra wells"}) == "keep"
    assert compact_unlisted_policy({"unlisted_wells_policy": "оставь"}) == "keep"
    assert compact_unlisted_policy({"unlisted_wells_policy": "убери"}) == "remove"
    assert compact_unlisted_policy({"Q-1": "please keep going"}) is None
    assert compact_unlisted_policy({"keep_unlisted": "ok"}) is None
    assert compact_unlisted_policy({"Q-1": {"unlisted_wells_policy": "keep"}}) == "keep"
    assert compact_unlisted_policy({"unlisted_wells_policy": {"raw": "remove extras"}}) == "remove"
    assert compact_unlisted_policy({"Q-1": {"raw": "keep extra wells"}}) is None
    # Option button from the Activity HITL panel
    assert compact_unlisted_policy({"unlisted_wells_policy": {"choice": "remove", "text": "Убрать из прогноза"}}) == "remove"
    assert compact_unlisted_policy({"Q-parent-group": {"choice": "remove", "text": "x"}}) is None
    assert is_unlisted_wells_gate("unlisted_wells_policy", "") is True
    assert is_unlisted_wells_gate("Q-1", "скважины не из excel") is True
    assert is_unlisted_wells_gate("Q-1", "") is False
