#!/usr/bin/env python3
"""Self-tests for check_schedule_includes.py (no third-party dependencies)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import check_schedule_includes as checker


def test_valid_virtual_package() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp)
        root = package / "SCHEDULE" / "FORECAST" / "MVP1_schedule_IN.INC"
        include = package / "INCLUDE" / "MODEL.INC"
        root.parent.mkdir(parents=True)
        include.parent.mkdir(parents=True)
        root.write_text("INCLUDE\n'../../INCLUDE/MODEL.INC' /\n", encoding="utf-8")
        include.write_text("DATES\n  1 JAN 2025 /\n", encoding="utf-8")
        code, result = checker.run(
            checker.build_parser().parse_args(
                [str(root), "--package-dir", str(package)]
            )
        )
        assert code == 0, result
        assert result["ok"] is True
        assert result["files_checked"] == 2
        assert result["includes_checked"] == 1


def test_invalid_include_and_encoding() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "MVP1_schedule_IN.INC"
        source.write_text("INCLUDE\n'' /\nINCLUDE\n'broken\n", encoding="utf-8")
        code, result = checker.run(
            checker.build_parser().parse_args([str(source)])
        )
        assert code == 1, result
        codes = {finding["code"] for finding in result["findings"]}
        assert "INCLUDE_PATH_INVALID" in codes
        assert "INCLUDE_QUOTE_UNTERMINATED" in codes

        utf16 = Path(tmp) / "utf16.INC"
        utf16.write_bytes("DATES\n/\n".encode("utf-16"))
        code, result = checker.run(
            checker.build_parser().parse_args([str(utf16)])
        )
        assert code == 1, result
        codes = {finding["code"] for finding in result["findings"]}
        assert "NUL_BYTE" in codes
        assert "ENCODING_NOT_UTF8" in codes


def test_missing_include_body() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp)
        root = package / "SCHEDULE" / "FORECAST" / "root.INC"
        root.parent.mkdir(parents=True)
        root.write_text("INCLUDE\n'../../INCLUDE/missing.INC' /\n", encoding="utf-8")
        code, result = checker.run(
            checker.build_parser().parse_args(
                [str(root), "--package-dir", str(package)]
            )
        )
        assert code == 1, result
        assert any(f["code"] == "INCLUDE_NOT_FOUND" for f in result["findings"])


if __name__ == "__main__":
    test_valid_virtual_package()
    test_invalid_include_and_encoding()
    test_missing_include_body()
    print("check_schedule_includes self-tests OK")
