"""Unified diff for SCHEDULE text."""

from __future__ import annotations

import difflib


def unified_diff(before: str, after: str, from_file: str = "schedule.inc", to_file: str = "schedule_out.inc") -> str:
    return "".join(
        difflib.unified_diff(
            before.replace("\r\n", "\n").splitlines(keepends=True),
            after.replace("\r\n", "\n").splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
        )
    )
