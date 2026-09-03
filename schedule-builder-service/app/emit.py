"""Emit SCHEDULE text: within-DATES order, record `/`, bare block `/`, then blank line."""

from __future__ import annotations

from .keywords import TABLE_KEYWORDS, normalize_keyword, within_date_rank
from .parse import Block, Record, ScheduleDoc, timeline_segments


def _record_line(record: Record) -> str:
    raw = record.raw.rstrip("\n")
    stripped = raw.strip()
    if not record.tokens and stripped.startswith("--"):
        return raw if raw.strip() else stripped
    if "/" in stripped:
        # Keep original slash placement (`1146 / J11c`, not `1146 J11c /`).
        line = raw.rstrip()
    elif record.tokens:
        line = "  " + " ".join(record.tokens) + " /"
    else:
        line = "  /"
    if record.comment and "--" not in line:
        line += f" -- {record.comment}"
    return line


def emit_block(block: Block) -> str:
    kw = normalize_keyword(block.keyword)
    lines = [kw]
    for record in block.records:
        lines.append(_record_line(record))
    if kw in TABLE_KEYWORDS or block.records:
        lines.append("/")
        lines.append("")
    elif block.raw_body.strip():
        body = block.raw_body.rstrip()
        if not body.endswith("/"):
            lines.extend(body.split("\n"))
            lines.append("/")
            lines.append("")
        else:
            lines.extend(body.split("\n"))
            if not lines[-1].strip():
                pass
            else:
                lines.append("")
    else:
        lines.append("/")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def emit_schedule(doc: ScheduleDoc) -> str:
    segments = timeline_segments(doc)
    if not segments:
        return "".join(emit_block(block) for block in doc.blocks)
    parts: list[str] = []
    for _index, segment in enumerate(segments):
        dates_block = segment.get("dates_block")
        if dates_block is not None:
            parts.append(emit_block(dates_block))
        blocks: list[Block] = list(segment.get("blocks") or [])
        ranked = list(enumerate(blocks))
        ranked.sort(key=lambda pair: (within_date_rank(pair[1].keyword, pair[0]), pair[0]))
        for _, block in ranked:
            parts.append(emit_block(block))
    text = "".join(parts)
    if not text.endswith("\n"):
        text += "\n"
    return text
