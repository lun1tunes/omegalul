from __future__ import annotations

import pytest

from mas_agent_kit import (
    ToolError,
    agent_result,
    engineer_request_from_args,
    human_text_problems,
    in_progress,
    list_preview,
    needs_input,
    options_for_human,
    plural_ru,
    result_text_problems,
    slug,
)
from mas_agent_kit.hitl import engineer_answers, hitl_payloads


def test_human_text_gate_accepts_prose_and_rejects_machine_tokens() -> None:
    assert human_text_problems("Какую группу выбрать для скважин 1601 и H_304R: DKS или FIELD?") == []
    problems = human_text_problems("Укажите unlisted_wells_policy=keep или {json}")
    assert any("машинные токены" in p for p in problems)
    assert "вопрос должен быть по-русски" in human_text_problems("Choose a table please")
    assert "слишком коротко для вопроса инженеру" in human_text_problems("Да?")


def test_options_and_plural_helpers() -> None:
    assert options_for_human("Оставить; Убрать") == [{"value": "Оставить", "label": "Оставить"}, {"value": "Убрать", "label": "Убрать"}]
    assert options_for_human('[{"value":"keep","label":"Оставить","hint":"как в baseline"}]') == [
        {"value": "keep", "label": "Оставить", "hint": "как в baseline"}
    ]
    assert options_for_human(None) == []
    assert plural_ru(1, "скважина", "скважины", "скважин") == "1 скважина"
    assert plural_ru(3, "скважина", "скважины", "скважин") == "3 скважины"
    assert plural_ru(11, "скважина", "скважины", "скважин") == "11 скважин"
    assert list_preview(["a", "b", "c"], limit=2) == "a, b и ещё 1"
    assert slug("Target group!") == "target_group"
    assert slug("", "excel") == "excel"


def test_agent_result_contract_and_status_validation() -> None:
    result = agent_result("demo", "T-1", "completed", "Готово: 3 скважины.", data={"n": 3})
    assert set(result) == {"task_id", "agent_id", "status", "message", "data", "artifacts", "issues", "assumptions", "requests"}
    with pytest.raises(ValueError):
        agent_result("demo", "T-1", "done", "x")
    question = needs_input("demo", "T-1", "Какую дату ввода взять для скважины 1601?", question_id="Q-date", accepts_files=["xlsx"])
    assert question["status"] == "needs_input"
    assert question["requests"][0]["question_id"] == "Q-date"
    assert question["requests"][0]["accepts"] == {"free_text": True, "files": ["xlsx"]}
    assert result_text_problems(question) == []
    bad = agent_result("demo", "T-1", "needs_input", "table_id=3", requests=[{"question": "select table_id", "options": [{"label": "a|b"}]}])
    assert result_text_problems(bad)
    # Phase 4.5: long jobs return in_progress with a free-form watch descriptor; other statuses carry no watch key.
    long = in_progress("demo", "T-1", "Расчёт запущен, около двух минут.", watch={"kind": "poll", "ref": "job-1", "poll_hint": "20s"})
    assert long["status"] == "in_progress" and long["watch"] == {"kind": "poll", "ref": "job-1", "poll_hint": "20s"}
    assert "watch" not in result and result_text_problems(long) == []


def test_engineer_request_from_args_validates_like_ask_engineer() -> None:
    with pytest.raises(ToolError) as exc:
        engineer_request_from_args({"question": "Укажите date_column для таблицы"}, default_topic="excel")
    assert exc.value.code == "question_not_human"
    assert exc.value.envelope()["ok"] is False and exc.value.envelope()["problems"]
    req = engineer_request_from_args(
        {"question": "В какой колонке новая дата ввода: «План 2026» или «Факт»?", "options": "План 2026; Факт", "topic": "Date Column", "accepts_files": "xlsx, .inc"},
        default_topic="excel",
    )
    assert req["question_id"] == "Q-date_column"
    assert req["type"] == "choice" and [o["label"] for o in req["options"]] == ["План 2026", "Факт"]
    assert req["accepts"]["files"] == ["xlsx", ".inc"]


def test_hitl_answers_are_compact_for_the_llm() -> None:
    context = {
        "hitl": {
            "answers": {
                "Q-unlisted": '{"choice":"keep","label":"Оставить как в baseline"}',
                "Q-facts": {"text": "1601 — 1 мар 2026", "new_wells": [{"well": "N1"}]},
                "Q-free": "просто текст",
                "Q-empty": "",
            }
        }
    }
    rows = engineer_answers({"context": context})
    assert rows[0] == {"question_id": "Q-unlisted", "choice": "keep", "label": "Оставить как в baseline"}
    assert rows[1]["attached_facts"] == "new_wells: 1 записей"
    assert rows[2] == {"question_id": "Q-free", "text": "просто текст"}
    assert len(rows) == 3
    payloads = hitl_payloads(context)
    assert payloads[0]["choice"] == "keep" and payloads[2] == "просто текст"
