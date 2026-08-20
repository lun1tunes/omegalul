"""Lexical SCHEDULE parser: keyword blocks + record lines. Does not invent field layouts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .keywords import KEYWORDS, TABLE_KEYWORDS, normalize_keyword

HEADER = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*(?:--.*)?$")


@dataclass
class Record:
    tokens: list[str]
    raw: str


@dataclass
class Block:
    keyword: str
    records: list[Record] = field(default_factory=list)
    raw_body: str = ""
    known: bool = True


@dataclass
class ScheduleDoc:
    blocks: list[Block]
    text: str
    sha256: str


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_schedule(text: str) -> ScheduleDoc:
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    headers: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        match = HEADER.match(line)
        if not match:
            continue
        token = match.group(1)
        kw = normalize_keyword(token)
        if kw in KEYWORDS or (token.isupper() and len(token) >= 3):
            headers.append((i, kw))
    blocks: list[Block] = []
    for idx, (start, kw) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        body_lines = lines[start + 1 : end]
        records: list[Record] = []
        for raw in body_lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith("--") or stripped == "/":
                continue
            cleaned = stripped[:-1].rstrip() if stripped.endswith("/") else stripped
            tokens = [tok for tok in re.split(r"[,\s]+", cleaned) if tok and tok != "/"]
            if tokens:
                records.append(Record(tokens=tokens, raw=raw.rstrip("\n")))
        blocks.append(
            Block(
                keyword=kw,
                records=records,
                raw_body="\n".join(body_lines),
                known=kw in KEYWORDS,
            )
        )
    return ScheduleDoc(blocks=blocks, text=source, sha256=_sha(source))


def timeline_segments(doc: ScheduleDoc) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for block in doc.blocks:
        if block.keyword == "DATES":
            if current:
                segments.append(current)
            date = block.records[0].tokens[0] if block.records else None
            current = {"date": date, "dates_block": block, "blocks": []}
            continue
        if current is None:
            current = {"date": None, "dates_block": None, "blocks": []}
        current["blocks"].append(block)
    if current:
        segments.append(current)
    return segments


def well_names(doc: ScheduleDoc) -> set[str]:
    names: set[str] = set()
    for block in doc.blocks:
        if block.keyword in TABLE_KEYWORDS and block.keyword != "DATES":
            for rec in block.records:
                if rec.tokens:
                    names.add(rec.tokens[0].strip("'").strip('"'))
    return names
