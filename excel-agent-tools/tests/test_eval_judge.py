from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_eval():
    path = Path(__file__).resolve().parent / "fixtures" / "real-public" / "run_agent_eval.py"
    spec = importlib.util.spec_from_file_location("run_agent_eval", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_judge_rejects_extra_columns_and_weak_oil_guess() -> None:
    eval_mod = _load_eval()
    leak = eval_mod.judge(
        {
            "expect": {
                "status_in": ["success", "partial"],
                "column_hints": ["Natural gas, Europe"],
                "forbidden_column_hints": ["Banana"],
                "allowed_column_substrings": ["Natural gas, Europe"],
                "max_stored_rows": 12,
            }
        },
        {
            "status": "partial",
            "columns": ["Column A", "Natural gas, Europe", "Banana, Europe"],
            "stored_rows": 12,
            "returned_count": 12,
            "provenance": [{"sheet": "Monthly Prices"}],
        },
    )
    assert leak["ok"] is False
    assert any("Banana" in reason or "extra column" in reason for reason in leak["reasons"])

    oil = eval_mod.judge(
        {"expect": {"status_in": ["clarification_needed"]}},
        {"status": "partial", "columns": ["Crude oil, Brent"], "stored_rows": 799},
    )
    assert oil["ok"] is False

    unemployment = eval_mod.judge(
        {
            "expect": {
                "status_in": ["success", "partial"],
                "column_hints": ["Unemployment"],
                "forbidden_column_hints": ["Employment rate"],
                "sheet_hints": ["neast_p"],
                "max_stored_rows": 1,
            }
        },
        {
            "status": "partial",
            "columns": ["Column A", "All 16 & over — Unemployment rate (%)"],
            "stored_rows": 1,
            "provenance": [{"sheet": "neast_p", "range": "A7:S427"}],
        },
    )
    assert unemployment["ok"] is True
