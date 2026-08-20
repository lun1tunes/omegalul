"""SCHEDULE validation: allowlist, DATES order, block terminators."""

from __future__ import annotations

import re
from typing import Any

from .keywords import KEYWORDS, TABLE_KEYWORDS, normalize_keyword
from .parse import ScheduleDoc

MONTH = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def _parse_date(raw: str) -> tuple[int, int, int] | None:
    text = raw.strip().upper().replace("'", "")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.match(r"^(\d{1,2})\s+([A-Z]{3})\s+(\d{4})$", text)
    if m and m.group(2) in MONTH:
        return int(m.group(3)), MONTH[m.group(2)], int(m.group(1))
    return None


def validate_emitted(text: str, doc: ScheduleDoc | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    dates: list[tuple[int, int, int]] = []
    while i < len(lines):
        line = lines[i].strip()
        if re.match(r"^[A-Z][A-Z0-9_]*$", line):
            kw = normalize_keyword(line)
            if kw not in KEYWORDS:
                findings.append({"code": "KEYWORD_UNSUPPORTED", "keyword": kw, "severity": "error", "line": i + 1})
            if kw in TABLE_KEYWORDS:
                j = i + 1
                saw_block = False
                while j < len(lines):
                    nxt = lines[j].strip()
                    if re.match(r"^[A-Z][A-Z0-9_]*$", nxt):
                        break
                    if nxt == "/":
                        saw_block = True
                        if j + 1 < len(lines) and lines[j + 1].strip() != "":
                            findings.append(
                                {
                                    "code": "BLOCK_TERMINATOR_SPACING",
                                    "keyword": kw,
                                    "severity": "warning",
                                    "line": j + 1,
                                }
                            )
                        break
                    j += 1
                if not saw_block:
                    findings.append({"code": "BLOCK_TERMINATOR_MISSING", "keyword": kw, "severity": "error", "line": i + 1})
            if kw == "DATES":
                j = i + 1
                while j < len(lines) and not re.match(r"^[A-Z][A-Z0-9_]*$", lines[j].strip()) and lines[j].strip() != "/":
                    candidate = lines[j].split("--")[0].replace("/", "").strip()
                    if candidate:
                        parsed = _parse_date(candidate)
                        if parsed:
                            dates.append(parsed)
                        else:
                            findings.append({"code": "DATES_VALUE_INVALID", "value": candidate, "severity": "error", "line": j + 1})
                    j += 1
        i += 1
    for a, b in zip(dates, dates[1:]):
        if b <= a:
            findings.append({"code": "DATES_NOT_STRICTLY_INCREASING", "severity": "error"})
            break
    return findings
