"""Python-native timeline edits used by Schedule Builder."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import re
from typing import Any

from .diff import unified_diff
from .emit import emit_schedule
from .parse import Block, Record, ScheduleDoc, parse_schedule, timeline_segments
from .well_model import is_factual_record

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
MOVE_KEYWORDS = {"WCONPROD", "WELOPEN", "WEFAC"}


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip().upper().replace(",", " ")
    text = re.sub(r"\s+", " ", text)
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    parts = text.split(" ")
    if len(parts) == 3 and parts[1] in MONTHS:
        try:
            return date(int(parts[2]), MONTHS[parts[1]], int(parts[0]))
        except ValueError:
            return None
    return None


def tnav_date(value: date) -> str:
    return f"{value.day} {value.strftime('%b').upper()} {value.year}"


def _well(record: Record) -> str:
    return record.tokens[0].strip("'\"") if record.tokens else ""


def _new_block(keyword: str, records: list[Record] | None = None) -> Block:
    return Block(keyword=keyword, records=records or [], raw_body="", known=True)


def _find_or_create_segment(segments: list[dict[str, Any]], target: date) -> dict[str, Any]:
    for segment in segments:
        current = parse_date(segment.get("date"))
        if current == target:
            return segment
    dates = _new_block("DATES", [Record(tokens=[tnav_date(target)], raw=f"  {tnav_date(target)} /")])
    segment = {"date": tnav_date(target), "dates_block": dates, "blocks": []}
    segments.append(segment)
    segments.sort(key=lambda item: parse_date(item.get("date")) or date.min)
    return segment


def _emit_segments(segments: list[dict[str, Any]]) -> str:
    blocks: list[Block] = []
    for segment in segments:
        dates_block = segment.get("dates_block")
        if dates_block is not None:
            blocks.append(dates_block)
        blocks.extend(segment.get("blocks") or [])
    return emit_schedule(ScheduleDoc(blocks=blocks, text="", sha256=""))


def commissioning_revise(
    source_text: str,
    well_facts: list[dict[str, Any]],
    *,
    file_ref: str = "schedule.inc",
) -> dict[str, Any]:
    targets: dict[str, date] = {}
    findings: list[dict[str, Any]] = []
    for fact in well_facts or []:
        well = str(fact.get("well") or fact.get("entity") or "").strip()
        target = parse_date(fact.get("date", fact.get("value")))
        if well and target:
            targets[well] = target
        elif well:
            findings.append({"code": "COMMISSIONING_DATE_INVALID", "severity": "error", "well": well})
    if not targets:
        return {
            "contract": "schedule_commissioning_revise_result",
            "status": "noop",
            "generated_schedule": source_text,
            "edits": [],
            "moved": [],
            "shifts": [],
            "findings": findings,
        }

    segments = deepcopy(timeline_segments(parse_schedule(source_text)))
    by_date = {parse_date(s.get("date")): s for s in segments if parse_date(s.get("date"))}
    moved: list[tuple[str, Record, str, date]] = []
    first_wconprod_date: dict[str, date | None] = {}
    for segment in segments:
        source_date = parse_date(segment.get("date"))
        for block in segment.get("blocks") or []:
            if block.keyword != "WCONPROD":
                continue
            for record in block.records:
                well = _well(record)
                if (
                    well in targets
                    and well not in first_wconprod_date
                    and not is_factual_record(record)
                ):
                    first_wconprod_date[well] = source_date
    missing_primary = sorted(set(targets) - set(first_wconprod_date))
    for well in missing_primary:
        findings.append({
            "code": "COMMISSIONING_PRIMARY_WCONPROD_MISSING",
            "severity": "error",
            "well": well,
            "keyword": "WCONPROD",
        })

    seen_primary: set[str] = set()
    seen_companions: set[tuple[str, str]] = set()
    for segment in segments:
        source_date = parse_date(segment.get("date"))
        for block in list(segment.get("blocks") or []):
            if block.keyword not in MOVE_KEYWORDS:
                continue
            keep: list[Record] = []
            for record in block.records:
                well = _well(record)
                if well not in targets:
                    keep.append(record)
                    continue
                is_primary = block.keyword == "WCONPROD"
                if is_primary and is_factual_record(record):
                    keep.append(record)
                    continue
                is_companion = (
                    block.keyword in {"WELOPEN", "WEFAC"}
                    and well in first_wconprod_date
                    and first_wconprod_date.get(well) == source_date
                )
                if (is_primary and well in seen_primary) or (
                    is_companion and (well, block.keyword) in seen_companions
                ) or (not is_primary and not is_companion):
                    keep.append(record)
                    continue
                if is_primary:
                    seen_primary.add(well)
                else:
                    seen_companions.add((well, block.keyword))
                moved.append((block.keyword, record, well, targets[well]))
            block.records = keep
            if not block.records:
                segment["blocks"].remove(block)

    for keyword, record, well, target in moved:
        destination = by_date.get(target)
        if destination is None:
            destination = _find_or_create_segment(segments, target)
            by_date[target] = destination
            findings.append({
                "code": "DATES_STEP_CREATED",
                "severity": "warning",
                "well": well,
                "dates": tnav_date(target),
            })
        block = next(
            (item for item in destination["blocks"] if item.keyword == keyword),
            None,
        )
        if block is None:
            block = _new_block(keyword)
            destination["blocks"].append(block)
        block.records.append(record)

    generated = _emit_segments(segments)
    hard = [item for item in findings if item.get("severity") == "error"]
    edits = [
        {
            "op": "retarget_record",
            "keyword": keyword,
            "entity": well,
            "to_dates_tnav": tnav_date(target),
        }
        for keyword, _record, well, target in moved
    ]
    return {
        "contract": "schedule_commissioning_revise_result",
        "contract_version": "1.0",
        "status": "needs_input" if hard else "applied",
        "generated_schedule": "" if hard else generated,
        "edits": edits,
        "moved": [{"well": w, "keyword": k, "to": tnav_date(t)} for k, _r, w, t in moved],
        "shifts": [{"well": w, "date": tnav_date(t)} for w, t in targets.items()],
        "findings": findings,
        "file_ref": file_ref,
        "control_semantics": {
            "commissioning_anchor": "first WCONPROD per well",
            "forecast_controls_preserved": True,
            "factual_wconprod_preserved": True,
            "forecast_control_count": max(0, sum(
                1 for segment in segments
                for block in segment.get("blocks") or []
                if block.keyword == "WCONPROD"
                for record in block.records
                if _well(record) in targets
            )),
        },
    }


def group_rebind_revise(
    source_text: str,
    spec: dict[str, Any],
    *,
    file_ref: str = "schedule.inc",
) -> dict[str, Any]:
    segments = deepcopy(timeline_segments(parse_schedule(source_text)))
    wells = {str(item).strip("'\"") for item in spec.get("wells") or []}
    parent = str(spec.get("parent_group") or "").upper()
    parent_of_parent = str(spec.get("parent_of_parent") or "").upper()
    control = str(spec.get("control") or "GRAT").upper()
    rate = spec.get("gas_rate", spec.get("rate"))
    missing = [name for name, value in (
        ("wells", wells), ("parent_group", parent), ("parent_of_parent", parent_of_parent), ("rate", rate)
    ) if not value]
    if missing:
        return {
            "contract": "schedule_group_rebind_revise_result",
            "status": "needs_input",
            "generated_schedule": "",
            "findings": [{"code": "GROUP_REBIND_SPEC_REQUIRED", "severity": "error", "missing": missing}],
            "wells": sorted(wells),
        }

    for segment in segments:
        for block in segment.get("blocks") or []:
            if block.keyword != "WELSPECS":
                continue
            for record in block.records:
                if _well(record) in wells:
                    if len(record.tokens) < 2:
                        record.tokens.append(f"'{parent}'")
                    else:
                        record.tokens[1] = f"'{parent}'"
                    record.raw = "  " + " ".join(record.tokens) + " /"

    root = next((segment for segment in segments if segment.get("dates_block") is None), None)
    if root is None:
        root = {"date": None, "dates_block": None, "blocks": []}
        segments.insert(0, root)
    tree = next((block for block in root["blocks"] if block.keyword == "GRUPTREE"), None)
    if tree is None:
        tree = _new_block("GRUPTREE")
        root["blocks"].insert(0, tree)
    if not any(record.tokens[:2] == [parent, parent_of_parent] for record in tree.records):
        tree.records.append(Record(tokens=[parent, parent_of_parent], raw=f"  {parent} {parent_of_parent} /"))

    target = next((segment for segment in segments if segment.get("dates_block") is not None), None)
    if target is None:
        target = _find_or_create_segment(segments, date.today())
    gcon = next((block for block in target["blocks"] if block.keyword == "GCONPROD"), None)
    if gcon is None:
        gcon = _new_block("GCONPROD")
        target["blocks"].append(gcon)
    rate_text = str(int(rate)) if isinstance(rate, (int, float)) and float(rate).is_integer() else str(rate)
    gcon.records.append(Record(
        tokens=[parent, control, rate_text],
        raw=f"  {parent} {control} {rate_text} /",
    ))
    generated = _emit_segments(segments)
    return {
        "contract": "schedule_group_rebind_revise_result",
        "contract_version": "1.0",
        "status": "applied",
        "generated_schedule": generated,
        "findings": [],
        "edits": [{"keyword": "WELSPECS", "wells": sorted(wells)}, {"keyword": "GCONPROD", "group": parent}],
        "wells": sorted(wells),
        "parent_group": parent,
        "gas_rate": rate,
        "file_ref": file_ref,
    }
