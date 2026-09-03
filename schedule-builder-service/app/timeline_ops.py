"""Python-native timeline edits used by Schedule Builder."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import re
from typing import Any

from .emit import emit_schedule
from .parse import Block, Record, ScheduleDoc, parse_schedule, timeline_segments
from .well_model import is_factual_record

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
MOVE_KEYWORDS = {"WCONPROD", "WELOPEN", "WEFAC"}
# Prose may *signal* remove. Never apply as authority — explicit enum or HITL.
_UNLISTED_REMOVE_RE = (
    re.compile(
        r"(убрать|убери|убрат|удал|remove|drop|delete).{0,120}"
        r"(нет в (файле|excel|книге|workbook)|not (present |listed )?in (the )?(excel|file|workbook|xlsx))"
    ),
    re.compile(
        r"(нет в (файле|excel|книге|workbook).{0,80}(убрать|убери|убрат|удал)|"
        r"скважин.{0,40}нет в (файле|excel).{0,40}(убрать|убери|убрат|удал))"
    ),
    re.compile(
        r"(wells?.{0,60}(not|absent).{0,40}(excel|file|workbook).{0,40}"
        r"(remove|drop|delete|убрать|убери|убрат))"
    ),
    re.compile(r"unlisted_wells_policy\s*[:=]\s*remove"),
    re.compile(r"\bremove_unlisted\b"),
)


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    text = text.upper().replace(",", " ")
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


def _well_name(value: Any) -> str:
    return str(value or "").strip().strip("'\"")


def detect_unlisted_wells_policy(blob: str) -> str:
    """Signal-only: prose may suggest remove. Not authority."""
    text = re.sub(r"\s+", " ", str(blob or "")).strip().lower()
    if any(pattern.search(text) for pattern in _UNLISTED_REMOVE_RE):
        return "remove"
    return "keep"


def normalize_unlisted_wells_policy(raw: Any) -> str:
    policy = str(raw or "").strip().lower()
    return policy if policy in {"keep", "remove"} else ""


def _excel_wells(well_facts: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for fact in well_facts or []:
        well = _well_name(fact.get("well") or fact.get("entity"))
        if well and well not in seen:
            seen.append(well)
    return seen


def _list_baseline_commissioning_wells(segments: list[dict[str, Any]]) -> list[str]:
    wells: set[str] = set()
    for segment in segments:
        for block in segment.get("blocks") or []:
            if block.keyword not in MOVE_KEYWORDS:
                continue
            for record in block.records:
                well = _well(record)
                if well:
                    wells.add(well)
    return sorted(wells)


def _preamble(segments: list[dict[str, Any]]) -> dict[str, Any]:
    for segment in segments:
        if segment.get("dates_block") is None:
            return segment
    root = {"date": None, "dates_block": None, "blocks": []}
    segments.insert(0, root)
    return root


def _upsert_keyword_records(segment: dict[str, Any], keyword: str, records: list[Record]) -> None:
    if not records:
        return
    block = next((item for item in segment["blocks"] if item.keyword == keyword), None)
    if block is None:
        block = _new_block(keyword)
        segment["blocks"].append(block)
    block.records.extend(records)


def _record_from_typed_line(line: str) -> Record:
    text = str(line or "").rstrip()
    if not text.endswith("/"):
        text = f"{text} /"
    stripped = text.strip()
    cleaned = stripped[:-1].rstrip() if stripped.endswith("/") else stripped
    tokens = [tok for tok in re.split(r"[,\s]+", cleaned) if tok and tok != "/"]
    raw = text if text[:1].isspace() else f"  {text}"
    return Record(tokens=tokens, raw=raw)


def _remove_unlisted_commissioning(
    segments: list[dict[str, Any]],
    excel_wells: list[str],
) -> list[dict[str, Any]]:
    keep = {name for name in excel_wells if name}
    removed: list[dict[str, Any]] = []
    for segment in segments:
        source_date = segment.get("date")
        next_blocks: list[Block] = []
        for block in segment.get("blocks") or []:
            if block.keyword not in MOVE_KEYWORDS:
                next_blocks.append(block)
                continue
            kept: list[Record] = []
            for record in block.records:
                well = _well(record)
                if well and well not in keep:
                    removed.append({
                        "well": well,
                        "keyword": block.keyword,
                        "from": source_date,
                    })
                    continue
                kept.append(record)
            block.records = kept
            if block.records:
                next_blocks.append(block)
        segment["blocks"] = next_blocks
    return removed


def _build_new_well_evidence_gaps(
    new_wells: list[str],
    shifts: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for well in new_wells:
        at = (shifts.get(well) or {}).get("tnav") or "unknown"
        common = {"entity": well, "effective_at": at}
        gaps.append({
            **common,
            "keyword": "WELLTRACK",
            "field": "trajectory_file",
            "reason": "NEW_WELL_MISSING_WELLTRACK",
            "expected_format": "WELLTRACK .inc/.dev file attached via Human Gate (INCLUDE path)",
            "question": (
                f"Прикрепите WELLTRACK для новой скважины {well} (файл траектории). "
                "MAS подключит через INCLUDE."
            ),
        })
        gaps.append({
            **common,
            "keyword": "WELSPECS",
            "field": "well_header",
            "reason": "NEW_WELL_MISSING_WELSPECS",
            "expected_format": "WELSPECS row or xlsx sheet WELSPECS",
            "question": f"Нужны WELSPECS для {well} (группа, I/J, PHASE и т.д.).",
        })
        gaps.append({
            **common,
            "keyword": "COMPDATMD",
            "field": "perforation_md",
            "reason": "NEW_WELL_MISSING_COMPDATMD",
            "expected_format": "xlsx: Скважина, MD_TOP, MD_BOT (или COMPDATMD)",
            "question": f"Укажите интервалы перфорации (MD) для {well} в xlsx.",
        })
        gaps.append({
            **common,
            "keyword": "WCONPROD",
            "field": "control_line",
            "reason": "NEW_WELL_MISSING_CONTROL",
            "expected_format": "typed WCONPROD or WCONINJE record line (no default GRAT/rate)",
            "question": (
                f"Укажите стартовую строку WCONPROD или WCONINJE для {well} — без значений по умолчанию."
            ),
        })
    return gaps


def _apply_new_well_definitions(
    segments: list[dict[str, Any]],
    defs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applied: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    preamble = _preamble(segments)
    for defn in defs or []:
        well = _well_name(defn.get("well") or defn.get("entity"))
        if not well:
            continue
        target = parse_date(defn.get("date", defn.get("commissioning_date")))
        if target is None:
            findings.append({
                "code": "NEW_WELL_DATE_INVALID",
                "severity": "error",
                "well": well,
                "date": str(defn.get("date") or defn.get("commissioning_date") or ""),
            })
            continue
        include_path = _well_name(
            defn.get("welltrack_include") or defn.get("welltrack_path") or defn.get("include_path")
        )
        welspecs_line = str(defn.get("welspecs_line") or defn.get("welspecs") or "")
        if isinstance(defn.get("compdatmd_lines"), list):
            comp_lines = [str(item) for item in defn["compdatmd_lines"] if str(item).strip()]
        elif defn.get("compdatmd_line"):
            comp_lines = [str(defn["compdatmd_line"])]
        else:
            comp_lines = []
        open_line = str(defn.get("welopen_line") or "")
        wcon_prod = str(defn.get("wconprod_line") or "")
        wcon_inje = str(defn.get("wconinje_line") or "")
        wefac = str(defn.get("wefac_line") or "")
        has_typed = bool(
            include_path or welspecs_line or comp_lines or open_line or wcon_prod or wcon_inje or wefac
        )
        if not has_typed:
            findings.append({"code": "NEW_WELL_TYPED_LINES_REQUIRED", "severity": "error", "well": well})
            continue
        if include_path:
            preamble["blocks"].append(_new_block(
                "INCLUDE",
                [Record(tokens=[f"'{include_path}'"], raw=f"  '{include_path}' /")],
            ))
        if welspecs_line:
            _upsert_keyword_records(preamble, "WELSPECS", [_record_from_typed_line(welspecs_line)])
        step = _find_or_create_segment(segments, target)
        if comp_lines:
            _upsert_keyword_records(step, "COMPDATMD", [_record_from_typed_line(line) for line in comp_lines])
        if open_line:
            _upsert_keyword_records(step, "WELOPEN", [_record_from_typed_line(open_line)])
        if wcon_prod:
            _upsert_keyword_records(step, "WCONPROD", [_record_from_typed_line(wcon_prod)])
        elif wcon_inje:
            _upsert_keyword_records(step, "WCONINJE", [_record_from_typed_line(wcon_inje)])
        else:
            findings.append({"code": "NEW_WELL_CONTROL_LINE_REQUIRED", "severity": "error", "well": well})
        if wefac:
            _upsert_keyword_records(step, "WEFAC", [_record_from_typed_line(wefac)])
        if wcon_prod or wcon_inje:
            applied.append({"well": well, "iso": target.isoformat(), "tnav": tnav_date(target), "include": include_path or None})
    return applied, findings


def _hitl_result(
    *,
    file_ref: str,
    findings: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    unlisted: list[str],
    new_wells: list[str],
    policy: str | None,
    evidence_gap: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gap = evidence_gap or []
    continuation: dict[str, Any] = {
        "protocol": "schedule-builder-hitl-attachment-v1",
        "unlisted_wells": unlisted,
        "unlisted_wells_policy": policy,
        "new_wells": new_wells,
    }
    if gap:
        continuation["evidence_gap"] = gap
    return {
        "contract": "schedule_commissioning_revise_result",
        "contract_version": "1.0",
        "status": "needs_input",
        "generated_schedule": "",
        "edits": [],
        "moved": [],
        "shifts": [],
        "removed": [],
        "unlisted_wells_policy": policy,
        "unlisted_wells": unlisted,
        "new_wells": new_wells,
        "findings": findings,
        "questions": questions,
        "evidence_gap": gap,
        "continuation": continuation,
        "human_request": {"kind": "needs_input", "questions": questions},
        "file_ref": file_ref,
        "control_semantics": {
            "commissioning_anchor": "first WCONPROD per well",
            "forecast_controls_preserved": True,
            "factual_wconprod_preserved": True,
        },
    }


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


def _retarget_commissioning_dates(
    segments: list[dict[str, Any]],
    targets: dict[str, date],
    findings: list[dict[str, Any]],
) -> list[tuple[str, Record, str, date]]:
    if not targets:
        return []
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
    for well in sorted(set(targets) - set(first_wconprod_date)):
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
    return moved


def commissioning_revise(
    source_text: str,
    well_facts: list[dict[str, Any]],
    *,
    file_ref: str = "schedule.inc",
    unlisted_wells_policy: str | None = None,
    instruction_blob: str = "",
    new_well_defs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    all_targets: dict[str, date] = {}
    for fact in well_facts or []:
        well = _well_name(fact.get("well") or fact.get("entity"))
        target = parse_date(fact.get("date", fact.get("value")))
        if well and target:
            all_targets[well] = target
        elif well:
            findings.append({"code": "COMMISSIONING_DATE_INVALID", "severity": "error", "well": well})

    segments = deepcopy(timeline_segments(parse_schedule(source_text)))
    excel_wells = _excel_wells(well_facts)
    baseline_wells = _list_baseline_commissioning_wells(segments)
    baseline_set = set(baseline_wells)
    excel_set = set(excel_wells)
    unlisted = sorted(baseline_set - excel_set)
    new_wells = [well for well in excel_wells if well not in baseline_set]
    explicit_policy = normalize_unlisted_wells_policy(unlisted_wells_policy)
    prose_suggests_remove = detect_unlisted_wells_policy(instruction_blob) == "remove"

    if not explicit_policy and prose_suggests_remove and unlisted:
        questions = [{
            "id": "unlisted_wells_policy",
            "question": (
                f"В Excel нет скважин: {', '.join(unlisted[:20])}"
                f"{'…' if len(unlisted) > 20 else ''}. Сохранить их запуски или убрать?"
            ),
            "expected_format": "keep|remove",
            "required": True,
            "type": "enum",
            "enum": ["keep", "remove"],
        }]
        return _hitl_result(
            file_ref=file_ref,
            findings=[{
                "code": "UNLISTED_WELLS_POLICY_REQUIRED",
                "severity": "error",
                "wells": unlisted[:40],
                "note": "Prose suggested remove but unlisted_wells_policy enum is required; prose is not authority",
            }],
            questions=questions,
            unlisted=unlisted,
            new_wells=new_wells,
            policy=None,
        )

    policy = explicit_policy or "keep"
    shifts_preview = {
        well: {"iso": target.isoformat(), "tnav": tnav_date(target)}
        for well, target in all_targets.items()
    }
    defs = [row for row in (new_well_defs or []) if isinstance(row, dict)]
    def_wells = {_well_name(row.get("well") or row.get("entity")) for row in defs}
    def_wells.discard("")
    unresolved_new = [well for well in new_wells if well not in def_wells]
    if unresolved_new:
        gaps = _build_new_well_evidence_gaps(unresolved_new, shifts_preview)
        questions = [
            {
                "id": "new_wells_policy",
                "question": (
                    f"В Excel есть новые скважины ({', '.join(unresolved_new)}), которых нет в schedule. "
                    "Прикрепите траектории (WELLTRACK) и таблицу с перфорациями и стартовыми дебитами."
                ),
                "expected_format": "text + file attachments (WELLTRACK + xlsx)",
                "required": True,
                "type": "file",
            },
            *[
                {
                    "id": f"new_well_gap_{index + 1}",
                    "question": gap["question"],
                    "expected_format": gap["expected_format"],
                    "required": True,
                    "type": "file",
                }
                for index, gap in enumerate(gaps[:20])
            ],
        ]
        return _hitl_result(
            file_ref=file_ref,
            findings=[{
                "code": "NEW_WELLS_REQUIRE_HITL",
                "severity": "error",
                "wells": unresolved_new,
                "note": "Dates present but WELSPECS/WELLTRACK/COMPDATMD/WCONPROD start params missing",
            }],
            questions=questions,
            unlisted=unlisted,
            new_wells=unresolved_new,
            policy=policy,
            evidence_gap=gaps,
        )

    removed: list[dict[str, Any]] = []
    if policy == "remove" and unlisted:
        removed = _remove_unlisted_commissioning(segments, excel_wells)

    existing_targets = {well: target for well, target in all_targets.items() if well in baseline_set}
    moved = _retarget_commissioning_dates(segments, existing_targets, findings)
    new_applied: list[dict[str, Any]] = []
    if defs:
        new_applied, new_findings = _apply_new_well_definitions(segments, defs)
        findings.extend(new_findings)

    if removed:
        findings.append({
            "code": "UNLISTED_WELLS_REMOVED",
            "severity": "warning",
            "count": len(removed),
            "wells": sorted({row["well"] for row in removed})[:40],
        })
    if policy == "keep" and unlisted:
        findings.append({
            "code": "UNLISTED_WELLS_KEPT",
            "severity": "warning",
            "wells": unlisted[:40],
            "note": "Default: preserve starts for wells not in Excel",
        })
    if new_applied:
        findings.append({
            "code": "NEW_WELLS_APPLIED",
            "severity": "warning",
            "wells": [row["well"] for row in new_applied],
        })

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
    if hard:
        status = "needs_input"
        generated = ""
    elif not moved and not removed and not new_applied:
        status = "noop"
        generated = source_text if not existing_targets and not defs else generated
    else:
        status = "applied"
    return {
        "contract": "schedule_commissioning_revise_result",
        "contract_version": "1.0",
        "status": status,
        "generated_schedule": generated,
        "edits": edits,
        "moved": [{"well": w, "keyword": k, "to": tnav_date(t)} for k, _r, w, t in moved],
        "shifts": [{"well": w, "date": tnav_date(t)} for w, t in existing_targets.items()],
        "removed": removed,
        "new_wells_applied": new_applied,
        "unlisted_wells_policy": policy,
        "unlisted_wells": unlisted,
        "new_wells": new_wells,
        "findings": findings,
        "questions": [],
        "evidence_gap": [],
        "continuation": None,
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
                if _well(record) in existing_targets
            )),
        },
    }


def _first_commissioning_date_for_wells(
    segments: list[dict[str, Any]],
    wells: set[str],
) -> date | None:
    best: date | None = None
    for segment in segments:
        source_date = parse_date(segment.get("date"))
        if source_date is None:
            continue
        for block in segment.get("blocks") or []:
            if block.keyword != "WCONPROD":
                continue
            for record in block.records:
                if _well(record) in wells:
                    if best is None or source_date < best:
                        best = source_date
    return best


def _copy_first_keyword_records(
    segments: list[dict[str, Any]],
    keyword: str,
    wells: set[str],
) -> list[Record]:
    seen: set[str] = set()
    out: list[Record] = []
    for segment in segments:
        for block in segment.get("blocks") or []:
            if block.keyword != keyword:
                continue
            for record in block.records:
                well = _well(record)
                if well in wells and well not in seen and record.tokens:
                    seen.add(well)
                    out.append(Record(
                        tokens=list(record.tokens),
                        raw=record.raw,
                        comment=record.comment,
                    ))
    return out


def group_rebind_revise(
    source_text: str,
    spec: dict[str, Any],
    *,
    file_ref: str = "schedule.inc",
) -> dict[str, Any]:
    segments = deepcopy(timeline_segments(parse_schedule(source_text)))
    wells = [str(item).strip("'\"") for item in spec.get("wells") or [] if str(item).strip()]
    well_set = set(wells)
    parent = str(spec.get("parent_group") or "").upper()
    parent_of_parent = str(spec.get("parent_of_parent") or "").upper()
    control = str(spec.get("control") or "").upper() or "GRAT"
    rate = spec.get("gas_rate", spec.get("rate"))
    groups_in = spec.get("well_groups") if isinstance(spec.get("well_groups"), dict) else {}
    well_groups = {str(key): str(value).strip() for key, value in groups_in.items() if str(value).strip()}
    missing = [name for name, value in (
        ("wells", wells),
        ("parent_group", parent),
        ("parent_of_parent", parent_of_parent),
        ("control", control),
        ("rate", rate),
    ) if not value]
    if wells and any(not well_groups.get(well) for well in wells):
        missing.append("well_groups")
    if missing:
        return {
            "contract": "schedule_group_rebind_revise_result",
            "status": "needs_input",
            "generated_schedule": "",
            "findings": [{"code": "GROUP_REBIND_SPEC_REQUIRED", "severity": "error", "missing": missing}],
            "wells": wells,
        }

    try:
        rate_n = float(rate)
    except (TypeError, ValueError):
        rate_n = 0.0
    if rate_n <= 0:
        return {
            "contract": "schedule_group_rebind_revise_result",
            "status": "needs_input",
            "generated_schedule": "",
            "findings": [{"code": "GROUP_REBIND_SPEC_REQUIRED", "severity": "error", "missing": ["rate"]}],
            "wells": wells,
        }

    target_date = parse_date(spec.get("effective_at")) or _first_commissioning_date_for_wells(segments, well_set)
    if target_date is None:
        return {
            "contract": "schedule_group_rebind_revise_result",
            "status": "needs_input",
            "generated_schedule": "",
            "findings": [{
                "code": "GROUP_REBIND_COMMISSIONING_DATE_MISSING",
                "severity": "error",
                "wells": wells,
            }],
            "wells": wells,
        }

    wecon = _copy_first_keyword_records(segments, "WECON", well_set)
    wpimult = _copy_first_keyword_records(segments, "WPIMULT", well_set)
    step = _find_or_create_segment(segments, target_date)
    rate_text = str(int(rate_n)) if float(rate_n).is_integer() else str(rate_n)
    _upsert_keyword_records(step, "WELSPECS", [
        Record(tokens=[well, well_groups[well]], raw=f"  {well} {well_groups[well]} /")
        for well in wells
    ])
    tree_records = [
        Record(tokens=[parent, parent_of_parent], raw=f"  {parent} {parent_of_parent} /"),
        *[
            Record(tokens=[well_groups[well], parent], raw=f"  {well_groups[well]} {parent} /")
            for well in wells
        ],
    ]
    _upsert_keyword_records(step, "GRUPTREE", tree_records)
    _upsert_keyword_records(step, "GCONPROD", [
        Record(tokens=[parent, control, "2*", rate_text], raw=f"  {parent} {control} 2* {rate_text} /"),
    ])
    edits = [
        {"op": "add", "keyword": "WELSPECS", "entities": list(wells)},
        {"op": "add", "keyword": "GRUPTREE", "entity": parent},
        {"op": "add", "keyword": "GCONPROD", "entity": parent, "gas_rate": rate_n, "control": control},
    ]
    if wecon:
        _upsert_keyword_records(step, "WECON", wecon)
        edits.append({"op": "reemit", "keyword": "WECON", "entities": [_well(item) for item in wecon]})
    if wpimult:
        _upsert_keyword_records(step, "WPIMULT", wpimult)
        edits.append({"op": "reemit", "keyword": "WPIMULT", "entities": [_well(item) for item in wpimult]})

    generated = _emit_segments(segments)
    return {
        "contract": "schedule_group_rebind_revise_result",
        "contract_version": "1.0",
        "status": "applied",
        "generated_schedule": generated,
        "findings": [],
        "edits": edits,
        "wells": wells,
        "parent_group": parent,
        "parent_of_parent": parent_of_parent,
        "gas_rate": rate_n,
        "control": control,
        "dates_tnav": tnav_date(target_date),
        "file_ref": file_ref,
    }
